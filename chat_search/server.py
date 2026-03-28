"""Flask web server for multi-chat search UI."""

import os
import functools
import mimetypes
from flask import Flask, request, jsonify, render_template, send_from_directory, abort, Response

from . import indexer, ai_chat, config

# Register .opus MIME type so browsers can play audio
mimetypes.add_type("audio/ogg", ".opus")


def create_app(chats_dir: str) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["CHATS_DIR"] = chats_dir
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB max upload

    # ------------------------------------------------------------------
    # Auth helpers (Supabase JWT verification)
    # ------------------------------------------------------------------

    def _get_supabase_client():
        """Lazy-init Supabase client."""
        if not hasattr(app, '_supabase'):
            url = os.environ.get("SUPABASE_URL", "")
            key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            if url and key:
                from supabase import create_client
                from supabase.lib.client_options import SyncClientOptions
                app._supabase = create_client(url, key, options=SyncClientOptions(flow_type="implicit"))
            else:
                app._supabase = None
        return app._supabase

    def _is_web_mode():
        """Check if running in web/SaaS mode (deployed on Railway with Supabase).
        Returns False when running locally even if SUPABASE_URL is in .env."""
        if not os.environ.get("SUPABASE_URL"):
            return False
        # On Railway, RAILWAY_ENVIRONMENT is always set
        # Locally, allow override via WEB_MODE=1
        return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("WEB_MODE"))

    def get_current_user():
        """Extract and verify user from Authorization header. Returns user dict or None."""
        if not _is_web_mode():
            return None  # Local mode - no auth needed

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:]
        sb = _get_supabase_client()
        if not sb:
            return None

        try:
            user = sb.auth.get_user(token)
            return {"id": user.user.id, "email": user.user.email}
        except Exception:
            return None

    ADMIN_EMAIL = "susunoufi@gmail.com"
    DEFAULT_USER_PRESET = "budget"  # New users get the cheapest plan

    def require_auth(f):
        """Decorator: require authentication in web mode. Skip in local/desktop mode."""
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not _is_web_mode():
                return f(*args, **kwargs)  # Local mode - no auth
            user = get_current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            request.user = user
            return f(*args, **kwargs)
        return decorated

    def require_admin(f):
        """Decorator: require admin authentication."""
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not _is_web_mode():
                return f(*args, **kwargs)  # Local mode - no restriction
            user = get_current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            if user.get("email") != ADMIN_EMAIL:
                return jsonify({"error": "Admin access required"}), 403
            request.user = user
            return f(*args, **kwargs)
        return decorated

    def get_user_plan(user_email: str) -> dict:
        """Get the full plan assigned to a user."""
        if user_email == ADMIN_EMAIL:
            return {"tier": "unlimited", "mode": "both", "cloud_preset": "premium", "local_vision": "proxy", "local_rag": "proxy"}
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        user_plans = settings.get("user_plans", {})
        return config.normalize_user_plan(user_plans.get(user_email))

    def get_user_preset(user_email: str) -> str:
        """Legacy helper: returns cloud_preset string for enforce_user_preset()."""
        return get_user_plan(user_email).get("cloud_preset", "budget")

    def get_user_api_keys(user_email: str) -> dict:
        """Get API keys that a specific user has set (stored per-user)."""
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        return settings.get("user_api_keys", {}).get(user_email, {})

    def get_allowed_providers_for_user(user) -> set:
        """Get allowed providers for current user (tier + own keys)."""
        if not user:
            return {"local", "ollama", "gemini", "openai", "anthropic"}  # Local mode = no restrictions
        plan = get_user_plan(user["email"])
        user_keys = get_user_api_keys(user["email"])
        return config.get_allowed_providers(user["email"], plan, user_keys)

    def enforce_user_tier():
        """In web mode, verify the user's selected provider is allowed by their tier.
        If not allowed, fall back to the best allowed provider."""
        if not _is_web_mode():
            return  # Local mode - no restriction
        user = get_current_user()
        if not user:
            return
        allowed = get_allowed_providers_for_user(user)
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        # Check each provider setting and fall back if not allowed
        for key in ("vision_provider", "video_provider", "rag_provider", "transcription_provider"):
            provider = settings.get(key, "")
            if provider and provider not in allowed and provider != "local":
                # Fall back: prefer gemini > openai > local
                if "gemini" in allowed:
                    settings[key] = "gemini"
                elif "openai" in allowed:
                    settings[key] = "openai"
                else:
                    settings[key] = "local"

    def get_user_chats(user) -> set:
        """Get the set of chat names owned by this user (from Supabase).
        In local mode, returns all chats."""
        if not _is_web_mode() or not user:
            return None  # None = no filtering (local mode)
        if user.get("email") == ADMIN_EMAIL:
            return None  # Admin sees everything
        try:
            rows = sb.table("user_chats").select("chat_name").eq("user_id", user["id"]).execute()
            return {r["chat_name"] for r in (rows.data or [])}
        except Exception:
            return set()

    def user_owns_chat(user, chat_name: str) -> bool:
        """Check if user owns a specific chat. In local mode, always True."""
        if not _is_web_mode():
            return True
        if not user:
            return False
        if user.get("email") == ADMIN_EMAIL:
            return True
        allowed = get_user_chats(user)
        return allowed is None or chat_name in allowed

    # ------------------------------------------------------------------
    # Auth endpoints
    # ------------------------------------------------------------------

    @app.route("/api/auth/signup", methods=["POST"])
    def auth_signup():
        """Sign up with email + password."""
        if not _is_web_mode():
            return jsonify({"error": "Auth not available in local mode"}), 400

        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        email = data.get("email", "").strip()
        password = data.get("password", "").strip()
        display_name = data.get("display_name", "").strip()

        if not email or not password:
            return jsonify({"error": "Email and password required"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        sb = _get_supabase_client()
        if not sb:
            return jsonify({"error": "Auth service unavailable"}), 503

        try:
            result = sb.auth.sign_up({
                "email": email,
                "password": password,
                "options": {"data": {"display_name": display_name or email.split("@")[0]}}
            })
            return jsonify({
                "user": {"id": result.user.id, "email": result.user.email},
                "session": {
                    "access_token": result.session.access_token,
                    "refresh_token": result.session.refresh_token,
                } if result.session else None,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/auth/login", methods=["POST"])
    def auth_login():
        """Login with email + password."""
        if not _is_web_mode():
            return jsonify({"error": "Auth not available in local mode"}), 400

        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        email = data.get("email", "").strip()
        password = data.get("password", "").strip()

        if not email or not password:
            return jsonify({"error": "Email and password required"}), 400

        sb = _get_supabase_client()
        if not sb:
            return jsonify({"error": "Auth service unavailable"}), 503

        try:
            result = sb.auth.sign_in_with_password({"email": email, "password": password})
            return jsonify({
                "user": {"id": result.user.id, "email": result.user.email},
                "session": {
                    "access_token": result.session.access_token,
                    "refresh_token": result.session.refresh_token,
                }
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 401

    @app.route("/api/auth/google")
    def auth_google():
        """Get Google OAuth URL for redirect."""
        if not _is_web_mode():
            return jsonify({"error": "Auth not available in local mode"}), 400

        sb = _get_supabase_client()
        if not sb:
            return jsonify({"error": "Auth service unavailable"}), 503

        try:
            redirect_url = request.args.get("redirect", request.host_url)
            result = sb.auth.sign_in_with_oauth({
                "provider": "google",
                "options": {"redirect_to": redirect_url}
            })
            return jsonify({"url": result.url})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/auth/refresh", methods=["POST"])
    def auth_refresh():
        """Refresh access token."""
        if not _is_web_mode():
            return jsonify({"error": "Auth not available in local mode"}), 400

        data = request.get_json()
        refresh_token = data.get("refresh_token", "") if data else ""
        if not refresh_token:
            return jsonify({"error": "refresh_token required"}), 400

        sb = _get_supabase_client()
        try:
            result = sb.auth.refresh_session(refresh_token)
            return jsonify({
                "session": {
                    "access_token": result.session.access_token,
                    "refresh_token": result.session.refresh_token,
                }
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 401

    @app.route("/api/auth/me")
    @require_auth
    def auth_me():
        """Get current user profile."""
        return jsonify(request.user)

    @app.route("/api/auth/logout", methods=["POST"])
    def auth_logout():
        """Logout (client-side token removal is sufficient, but this invalidates server-side)."""
        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Chat paths helper
    # ------------------------------------------------------------------

    def get_chat_paths(chat_name):
        """Return (chat_dir, db_path) for a given chat name, or abort 404."""
        chat_dir = os.path.join(chats_dir, chat_name)
        db_path = os.path.join(chat_dir, "data", "chat.db")
        if not os.path.isdir(chat_dir) or not os.path.exists(db_path):
            abort(404, f"Chat '{chat_name}' not found or not indexed")
        return chat_dir, db_path

    @app.route("/")
    def index():
        # In web mode, show landing page to unauthenticated users
        if _is_web_mode():
            user = get_current_user()
            if not user:
                return render_template("landing.html")
        return render_template("index.html")

    def _find_agent_dir():
        """Find the agent directory — works both locally and on Railway."""
        # Try relative to chats_dir (local dev: chats_dir = /project/chats)
        candidate = os.path.abspath(os.path.join(os.path.dirname(chats_dir), "agent"))
        if os.path.isdir(candidate):
            return candidate
        # Try relative to this file (chat_search/server.py -> ../agent)
        candidate = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")
        if os.path.isdir(candidate):
            return candidate
        # Railway: /app/agent
        if os.path.isdir("/app/agent"):
            return "/app/agent"
        return candidate  # fallback

    @app.route("/download/install.bat")
    def download_install_bat():
        """Serve the agent installer bat directly from the server."""
        return send_from_directory(_find_agent_dir(), "install.bat",
                                  as_attachment=True,
                                  download_name="WhatsArch-Agent-Install.bat")

    @app.route("/download/installer.py")
    def download_installer_py():
        """Serve the visual installer Python script."""
        return send_from_directory(_find_agent_dir(), "installer.py",
                                  as_attachment=True,
                                  download_name="installer.py")

    @app.route("/download/setup")
    def download_setup():
        """Serve the agent install.bat — one-click installer for local tools."""
        return send_from_directory(_find_agent_dir(), "install.bat",
                                  as_attachment=True,
                                  download_name="WhatsArch-Install.bat")

    @app.route("/app")
    def app_page():
        """Main app page. Auth checked client-side via JS token."""
        return render_template("index.html")

    @app.route("/privacy")
    def privacy_page():
        """Privacy policy page."""
        return render_template("privacy.html")

    @app.route("/login")
    def login_page():
        """Serve login/signup page (web mode only)."""
        if not _is_web_mode():
            return render_template("index.html")  # Local mode - skip auth
        return render_template("auth.html")

    @app.route("/auth/callback")
    def auth_callback():
        """OAuth callback - handles both PKCE (code in query) and implicit (token in hash)."""
        import json as _json
        code = request.args.get("code")
        if code:
            # PKCE flow: exchange code for session server-side
            sb = _get_supabase_client()
            if sb:
                try:
                    result = sb.auth.exchange_code_for_session({"auth_code": code})
                    if result.session:
                        # Safely encode tokens into JavaScript using json.dumps to prevent XSS
                        access_token = _json.dumps(result.session.access_token)
                        refresh_token = _json.dumps(result.session.refresh_token)
                        user_email = _json.dumps(result.user.email)
                        return f"""<!DOCTYPE html><html><head><title>WhatsArch</title></head><body>
                        <script>
                        localStorage.setItem('auth_token', {access_token});
                        localStorage.setItem('refresh_token', {refresh_token});
                        localStorage.setItem('user_email', {user_email});
                        window.location.href = '/app';
                        </script></body></html>"""
                except Exception as e:
                    error_msg = _json.dumps(str(e))
                    return f"""<!DOCTYPE html><html><body>
                    <p>Authentication error: <span id="err"></span></p>
                    <script>document.getElementById('err').textContent = {error_msg};</script>
                    <a href="/login">Try again</a></body></html>"""

        # Implicit flow: token in URL hash
        return """<!DOCTYPE html><html><head><title>WhatsArch</title></head><body>
        <script>
        const hash = window.location.hash.substring(1);
        const params = new URLSearchParams(hash);
        const token = params.get('access_token');
        const refresh = params.get('refresh_token');
        if (token) {
            localStorage.setItem('auth_token', token);
            if (refresh) localStorage.setItem('refresh_token', refresh);
            // Fetch user email from token
            fetch('/api/auth/me', {headers: {'Authorization': 'Bearer ' + token}})
                .then(r => r.json())
                .then(data => {
                    if (data.email) localStorage.setItem('user_email', data.email);
                })
                .finally(() => { window.location.href = '/app'; });
        } else {
            document.body.innerHTML = '<p>Authentication failed. <a href="/login">Try again</a></p>';
        }
        </script></body></html>"""

    @app.route("/api/chats/<chat_name>", methods=["DELETE"])
    @require_auth
    def api_delete_chat(chat_name):
        """Delete a chat and all its data."""
        import shutil
        chat_name = chat_name.strip()
        if not chat_name:
            abort(400, "Missing chat name")

        chat_dir = os.path.join(chats_dir, chat_name)
        if not os.path.isdir(chat_dir):
            abort(404, f"Chat '{chat_name}' not found")

        # Stop any running processing
        from . import process_manager
        process_manager.stop_processing(chat_name)

        # Delete from filesystem
        shutil.rmtree(chat_dir, ignore_errors=True)

        # Delete from Supabase + cloud storage if web mode
        user = getattr(request, "user", None)
        if user and _is_web_mode():
            sb = _get_supabase_client()
            if sb:
                # Delete from cloud storage
                try:
                    from . import storage
                    storage.delete_chat_storage(sb, user["id"], chat_name)
                except Exception as e:
                    print(f"[DELETE] Warning: Storage cleanup failed for {chat_name}: {e}")
                # Delete metadata from database
                try:
                    sb.table("user_chats").delete().eq("chat_name", chat_name).eq("user_id", user["id"]).execute()
                except Exception as e:
                    print(f"[DELETE] Warning: Supabase cleanup failed for {chat_name}: {e}")

        return jsonify({"status": "deleted", "chat": chat_name})

    @app.route("/api/chats")
    def api_chats():
        """List all available chats with their status (WhatsApp + Telegram)."""
        # Optional auth: filter by user in web mode, show all in local mode
        user = get_current_user()
        allowed_chats = get_user_chats(user) if user else None
        result = []
        for name in sorted(os.listdir(chats_dir)):
            if allowed_chats is not None and name not in allowed_chats:
                continue
            chat_dir = os.path.join(chats_dir, name)
            if not os.path.isdir(chat_dir):
                continue
            # Detect platform
            has_whatsapp = os.path.exists(os.path.join(chat_dir, "_chat.txt"))
            has_telegram = os.path.exists(os.path.join(chat_dir, "result.json"))
            if not has_whatsapp and not has_telegram:
                continue
            db_path = os.path.join(chat_dir, "data", "chat.db")
            ready = os.path.exists(db_path)
            platform = "telegram" if has_telegram else "whatsapp"
            info = {"name": name, "ready": ready, "platform": platform}
            if ready:
                stats = indexer.get_stats(db_path)
                info["total_messages"] = stats["total_messages"]
                metadata = indexer.get_chat_metadata(db_path)
                info["language"] = metadata.get("language", "he")
            result.append(info)
        return jsonify(result)

    @app.route("/api/search/all")
    @require_auth
    def api_search_all():
        """Search across all chats simultaneously."""
        q = request.args.get("q", "").strip()
        sender = request.args.get("sender", "").strip()
        date_from = request.args.get("from", "").strip()
        date_to = request.args.get("to", "").strip()
        search_type = request.args.get("type", "all").strip()
        try:
            page = int(request.args.get("page", 1))
        except (ValueError, TypeError):
            page = 1

        has_filters = sender or date_from or date_to or (search_type and search_type != "all")
        if not q and not has_filters:
            return jsonify({"results": [], "total": 0})

        all_results = []
        use_filtered = not q or q == "*"

        # Search each chat
        for name in sorted(os.listdir(chats_dir)):
            chat_dir = os.path.join(chats_dir, name)
            db_path = os.path.join(chat_dir, "data", "chat.db")
            if not os.path.isdir(chat_dir) or not os.path.exists(db_path):
                continue

            try:
                if use_filtered:
                    results, total = indexer.search_filtered(
                        db_path, sender=sender, date_from=date_from, date_to=date_to,
                        page=1, per_page=20, search_type=search_type
                    )
                else:
                    results, total = indexer.search(
                        db_path, q, sender=sender, date_from=date_from, date_to=date_to,
                        page=1, per_page=20, search_type=search_type
                    )
                for r in results:
                    r["chat_name"] = name
                all_results.extend(results)
            except Exception:
                continue

        # Sort all results by relevance_score (lower is better for FTS5 rank)
        all_results.sort(key=lambda r: r.get("relevance_score", 0))

        # Paginate
        per_page = 50
        start = (page - 1) * per_page
        end = start + per_page

        return jsonify({
            "results": all_results[start:end],
            "total": len(all_results),
            "page": page,
            "per_page": per_page,
        })

    @app.route("/api/search")
    @require_auth
    def api_search():
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            return jsonify({"results": [], "total": 0, "page": 1})

        _, db_path = get_chat_paths(chat_name)

        q = request.args.get("q", "").strip()
        sender = request.args.get("sender", "").strip()
        date_from = request.args.get("from", "").strip()
        date_to = request.args.get("to", "").strip()
        search_type = request.args.get("type", "all").strip()
        try:
            page = int(request.args.get("page", 1))
        except (ValueError, TypeError):
            page = 1

        if not q or q == "*":
            # If filters are active, use filter-only search instead of browse mode
            if sender or date_from or date_to or (search_type and search_type != "all"):
                results, total = indexer.search_filtered(
                    db_path, sender=sender, date_from=date_from, date_to=date_to,
                    page=page, search_type=search_type
                )
                return jsonify({"results": results, "total": total, "page": page, "per_page": 50})
            # Browse mode: show samples from each enriched category
            results, total = indexer.browse_enriched(db_path)
            return jsonify({"results": results, "total": total, "page": 1, "browse": True})

        results, total = indexer.search(
            db_path, q, sender=sender, date_from=date_from, date_to=date_to,
            page=page, search_type=search_type
        )

        return jsonify({
            "results": results,
            "total": total,
            "page": page,
            "per_page": 50,
        })

    @app.route("/api/context/<int:message_id>")
    @require_auth
    def api_context(message_id):
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        _, db_path = get_chat_paths(chat_name)

        try:
            before = int(request.args.get("before", 5))
        except (ValueError, TypeError):
            before = 5
        try:
            after = int(request.args.get("after", 5))
        except (ValueError, TypeError):
            after = 5
        messages = indexer.get_context(db_path, message_id, before, after)
        return jsonify({"messages": messages, "focus_id": message_id})

    @app.route("/api/stats")
    @require_auth
    def api_stats():
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        _, db_path = get_chat_paths(chat_name)
        stats = indexer.get_stats(db_path)

        # Add chunk count and chat metadata
        metadata = indexer.get_chat_metadata(db_path)
        stats["chat_type"] = metadata.get("chat_type", "1on1")
        stats["chunk_count"] = _get_chunk_count(db_path)
        return jsonify(stats)

    @app.route("/api/ai/status")
    @require_auth
    def api_ai_status():
        """Check AI provider availability."""
        info = ai_chat.LLMClient.get_provider_info()
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        info["current_rag_provider"] = settings.get("rag_provider")
        info["current_rag_model"] = settings.get("rag_model")
        return jsonify(info)

    @app.route("/api/ai/profile", methods=["POST"])
    @require_auth
    def api_ai_profile():
        """Generate group profile for a chat."""
        enforce_user_tier()
        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")
        chat_name = data.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat")
        chat_dir, db_path = get_chat_paths(chat_name)
        project_root = os.path.dirname(chats_dir)
        try:
            profile = ai_chat.generate_group_profile(db_path, chat_name, project_root)
            return jsonify({"status": "ok", "profile": profile})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/ai/profile", methods=["GET"])
    @require_auth
    def api_ai_profile_get():
        """Get existing group profile."""
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            return jsonify({"profile": ""})
        try:
            chat_dir, db_path = get_chat_paths(chat_name)
            profile = ai_chat.get_group_profile(db_path)
            return jsonify({"profile": profile})
        except Exception:
            return jsonify({"profile": ""})

    @app.route("/api/ai/chat", methods=["POST"])
    @require_auth
    def api_ai_chat():
        """AI chat endpoint - RAG pipeline."""
        enforce_user_tier()
        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        chat_name = data.get("chat", "").strip()
        question = data.get("question", "").strip()
        history = data.get("history", [])

        if not chat_name or not question:
            abort(400, "Missing chat or question")

        _, db_path = get_chat_paths(chat_name)

        try:
            project_root = os.path.dirname(chats_dir)
            metadata = indexer.get_chat_metadata(db_path)
            language = metadata.get("language", "he")
            result = ai_chat.ask(db_path, question, chat_name, history, project_root=project_root, language=language)
            return jsonify(result)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503
        except Exception as e:
            return jsonify({"error": f"שגיאה: {str(e)}"}), 500

    @app.route("/api/ai/chat/stream", methods=["POST"])
    @require_auth
    def api_ai_chat_stream():
        """Streaming AI chat endpoint using Server-Sent Events."""
        enforce_user_tier()
        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        chat_name = data.get("chat", "").strip()
        question = data.get("question", "").strip()
        history = data.get("history", [])

        if not chat_name or not question:
            abort(400, "Missing chat or question")

        _, db_path = get_chat_paths(chat_name)
        project_root = os.path.dirname(chats_dir)

        def generate():
            import json
            try:
                metadata = indexer.get_chat_metadata(db_path)
                language = metadata.get("language", "he")
                for chunk in ai_chat.ask_stream(db_path, question, chat_name, history, project_root=project_root, language=language):
                    yield f"data: {chunk}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return Response(generate(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.route("/api/vision/status")
    def api_vision_status():
        """Check vision processing status for a chat."""
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        chat_dir = os.path.join(chats_dir, chat_name)
        if not os.path.isdir(chat_dir):
            abort(404, f"Chat '{chat_name}' not found")

        from . import vision

        desc_cache_path = os.path.join(chat_dir, "data", "descriptions.json")
        pdf_cache_path = os.path.join(chat_dir, "data", "pdf_texts.json")

        desc_cache = vision.load_cache(desc_cache_path)
        pdf_cache = vision.load_cache(pdf_cache_path)

        # Count media files
        image_exts = ('.jpg', '.jpeg', '.png')
        video_exts = ('.mp4', '.mov')
        pdf_exts = ('.pdf',)

        image_count = 0
        video_count = 0
        pdf_count = 0
        for f in os.listdir(chat_dir):
            fl = f.lower()
            if any(fl.endswith(e) for e in image_exts) and 'sticker' not in fl:
                image_count += 1
            elif any(fl.endswith(e) for e in video_exts) and not fl.startswith('gif'):
                video_count += 1
            elif any(fl.endswith(e) for e in pdf_exts):
                pdf_count += 1

        return jsonify({
            "total_images": image_count,
            "total_videos": video_count,
            "total_pdfs": pdf_count,
            "processed_descriptions": len(desc_cache),
            "processed_pdfs": len(pdf_cache),
        })

    # ------------------------------------------------------------------
    # Video thumbnail endpoint
    # ------------------------------------------------------------------

    @app.route("/api/thumbnail/<path:chat_and_file>")
    def api_thumbnail(chat_and_file):
        """Serve a video thumbnail (first frame). Generates on demand, cached."""
        parts = chat_and_file.split("/", 1)
        if len(parts) != 2:
            abort(400, "Invalid path")
        chat_name, filename = parts
        # Sanitize to prevent path traversal
        chat_name = os.path.basename(chat_name)
        filename = os.path.basename(filename)
        chat_dir = os.path.join(chats_dir, chat_name)
        if not os.path.isdir(chat_dir):
            abort(404)

        thumb_dir = os.path.join(chat_dir, "data", "thumbnails")
        thumb_filename = os.path.splitext(filename)[0] + ".jpg"
        thumb_path = os.path.join(thumb_dir, thumb_filename)

        if not os.path.exists(thumb_path):
            video_path = os.path.join(chat_dir, filename)
            if not os.path.exists(video_path):
                abort(404)
            from . import process_manager
            success = process_manager.generate_video_thumbnail(video_path, thumb_path)
            if not success:
                abort(500, "Failed to generate thumbnail")

        return send_from_directory(thumb_dir, thumb_filename)

    # ------------------------------------------------------------------
    # Processing management endpoints
    # ------------------------------------------------------------------

    @app.route("/api/process/status")
    @require_auth
    def api_process_status():
        """Get full processing status for a chat (file counts, progress, per-file detail)."""
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        chat_dir = os.path.join(chats_dir, chat_name)
        if not os.path.isdir(chat_dir):
            abort(404, f"Chat '{chat_name}' not found")

        from . import process_manager
        status = process_manager.get_processing_status(chat_dir)
        return jsonify(status)

    @app.route("/api/process/start", methods=["POST"])
    @require_auth
    def api_process_start():
        """Trigger a background processing step for a chat."""
        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        chat_name = data.get("chat", "").strip()
        task = data.get("task", "").strip()

        if not chat_name or not task:
            abort(400, "Missing chat or task")

        valid_tasks = {"transcribe", "images", "videos", "pdfs", "index", "embeddings"}
        if task not in valid_tasks:
            abort(400, f"Invalid task. Must be one of: {', '.join(valid_tasks)}")

        chat_dir = os.path.join(chats_dir, chat_name)
        if not os.path.isdir(chat_dir):
            abort(404, f"Chat '{chat_name}' not found")

        from . import process_manager
        started = process_manager.start_processing(chat_name, task, chats_dir)
        if not started:
            return jsonify({"error": "Task already running for this chat"}), 409

        return jsonify({"status": "started", "task": task})

    @app.route("/api/process/progress")
    @require_auth
    def api_process_progress():
        """Lightweight polling endpoint for active task progress."""
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        from . import process_manager
        task_info = process_manager.get_task_status(chat_name)
        return jsonify(task_info or {"status": "idle"})

    @app.route("/api/process/debug")
    def api_process_debug():
        """Debug endpoint: show exactly what image processing would use."""
        chat_name = request.args.get("chat", "").strip()
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)

        result = {
            "vision_provider": settings.get("vision_provider"),
            "vision_model": settings.get("vision_model"),
            "settings_path": config.get_settings_path(project_root),
            "settings_exists": os.path.exists(config.get_settings_path(project_root)),
        }

        # Check API key
        provider = settings.get("vision_provider", "anthropic")
        key_map = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GOOGLE_API_KEY"}
        env_var = key_map.get(provider, "")
        api_key = os.environ.get(env_var, "")
        if not api_key and provider == "gemini":
            api_key = os.environ.get("GEMINI_API_KEY", "")
        result["api_key_env_var"] = env_var
        result["api_key_present"] = bool(api_key)
        result["api_key_preview"] = api_key[:8] + "..." if api_key else "MISSING"

        # Check chat
        if chat_name:
            chat_dir = os.path.join(chats_dir, chat_name)
            data_dir = os.path.join(chat_dir, "data")
            desc_path = os.path.join(data_dir, "descriptions.json")
            result["chat_dir_exists"] = os.path.isdir(chat_dir)
            result["data_dir_exists"] = os.path.isdir(data_dir)
            result["descriptions_path"] = desc_path
            result["descriptions_exists"] = os.path.exists(desc_path)
            if os.path.exists(desc_path):
                import json as _json
                with open(desc_path, "r", encoding="utf-8") as f:
                    descs = _json.load(f)
                result["descriptions_count"] = len(descs)

            # Count images
            import glob
            images = []
            for ext in ("*.jpg", "*.jpeg", "*.png"):
                images.extend(glob.glob(os.path.join(chat_dir, ext)))
            result["image_files_on_disk"] = len(images)

        return jsonify(result)

    @app.route("/api/process/stop", methods=["POST"])
    @require_auth
    def api_process_stop():
        """Request cancellation of a running processing task."""
        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        chat_name = data.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        from . import process_manager
        stopped = process_manager.stop_processing(chat_name)
        if not stopped:
            return jsonify({"error": "No running task for this chat"}), 404

        return jsonify({"status": "stopping", "chat": chat_name})

    # ------------------------------------------------------------------
    # Media gallery endpoint
    # ------------------------------------------------------------------

    @app.route("/api/media/list")
    @require_auth
    def api_media_list():
        """List all media files for a chat with metadata."""
        chat_name = request.args.get("chat", "").strip()
        media_type_filter = request.args.get("type", "all").strip()  # all, image, video
        try:
            page = int(request.args.get("page", 1))
        except (ValueError, TypeError):
            page = 1
        per_page = 50

        if not chat_name:
            abort(400, "Missing chat parameter")

        chat_dir = os.path.join(chats_dir, chat_name)
        if not os.path.isdir(chat_dir):
            abort(404)

        # Load description cache
        from . import vision
        desc_cache = vision.load_cache(os.path.join(chat_dir, "data", "descriptions.json"))

        media_files = []
        image_exts = ('.jpg', '.jpeg', '.png')
        video_exts = ('.mp4', '.mov')

        for f in sorted(os.listdir(chat_dir)):
            fl = f.lower()
            full_path = os.path.join(chat_dir, f)
            if not os.path.isfile(full_path):
                continue

            is_image = any(fl.endswith(e) for e in image_exts) and 'sticker' not in fl.upper()
            is_video = any(fl.endswith(e) for e in video_exts) and not fl.upper().startswith('GIF')

            if not is_image and not is_video:
                continue

            if media_type_filter == 'image' and not is_image:
                continue
            if media_type_filter == 'video' and not is_video:
                continue

            item = {
                "filename": f,
                "type": "image" if is_image else "video",
                "description": desc_cache.get(f, ""),
                "url": f"/media/{chat_name}/{f}",
            }
            if is_video:
                item["thumbnail_url"] = f"/api/thumbnail/{chat_name}/{f}"

            media_files.append(item)

        total = len(media_files)
        start = (page - 1) * per_page
        end = start + per_page

        return jsonify({
            "files": media_files[start:end],
            "total": total,
            "page": page,
            "per_page": per_page,
        })

    # ------------------------------------------------------------------
    # Analytics endpoint
    # ------------------------------------------------------------------

    @app.route("/api/analytics")
    @require_auth
    def api_analytics():
        """Get analytics data for a chat."""
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        _, db_path = get_chat_paths(chat_name)

        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            c = conn.cursor()

            # Messages per sender
            c.execute("SELECT sender, COUNT(*) as count FROM messages GROUP BY sender ORDER BY count DESC LIMIT 20")
            senders = [{"sender": row["sender"], "count": row["count"]} for row in c.fetchall()]

            # Messages per month (for activity chart)
            c.execute("""
                SELECT substr(datetime, 7, 4) || '-' || substr(datetime, 4, 2) as month,
                       COUNT(*) as count
                FROM messages
                WHERE datetime IS NOT NULL AND length(datetime) >= 10
                GROUP BY month
                ORDER BY month
            """)
            activity = [{"month": row["month"], "count": row["count"]} for row in c.fetchall()]

            # Messages per hour of day (for heatmap)
            c.execute("""
                SELECT CAST(substr(datetime, 12, 2) AS INTEGER) as hour, COUNT(*) as count
                FROM messages
                WHERE datetime IS NOT NULL AND length(datetime) >= 14
                GROUP BY hour
                ORDER BY hour
            """)
            hourly = [{"hour": row["hour"], "count": row["count"]} for row in c.fetchall()]

            # Media breakdown
            c.execute("SELECT media_type, COUNT(*) as count FROM messages WHERE media_type IS NOT NULL AND media_type != '' GROUP BY media_type")
            media = [{"type": row["media_type"], "count": row["count"]} for row in c.fetchall()]

            # Average message length per sender
            c.execute("SELECT sender, ROUND(AVG(length(text)), 0) as avg_len FROM messages WHERE text IS NOT NULL AND text != '' GROUP BY sender ORDER BY avg_len DESC LIMIT 10")
            msg_lengths = [{"sender": row["sender"], "avg_length": row["avg_len"]} for row in c.fetchall()]

            # Most active days (top 10)
            c.execute("""
                SELECT substr(datetime, 1, 10) as date, COUNT(*) as count
                FROM messages
                WHERE datetime IS NOT NULL
                GROUP BY date
                ORDER BY count DESC
                LIMIT 10
            """)
            busiest_days = [{"date": row["date"], "count": row["count"]} for row in c.fetchall()]

            return jsonify({
                "senders": senders,
                "activity": activity,
                "hourly": hourly,
                "media": media,
                "msg_lengths": msg_lengths,
                "busiest_days": busiest_days,
            })
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Usage tracking endpoint
    # ------------------------------------------------------------------

    @app.route("/api/usage")
    @require_auth
    def api_usage():
        """Get usage analytics: costs, token usage, processing log.

        Query params:
            chat - single chat name or comma-separated list of chat names
            user - filter by user email
        """
        from . import usage_tracker
        chat_param = request.args.get("chat", "").strip()
        user = request.args.get("user", "").strip() or None

        # Support comma-separated chat names for multi-chat queries
        chat_name = None
        if chat_param:
            parts = [p.strip() for p in chat_param.split(",") if p.strip()]
            chat_name = parts if len(parts) > 1 else (parts[0] if parts else None)

        return jsonify(usage_tracker.get_usage_report(
            os.path.dirname(chats_dir), chat_name=chat_name, user=user
        ))

    # ------------------------------------------------------------------
    # Export endpoint
    # ------------------------------------------------------------------

    @app.route("/api/export")
    @require_auth
    def api_export():
        """Export search results as CSV or JSON file download."""
        chat_name = request.args.get("chat", "").strip()
        q = request.args.get("q", "").strip()
        fmt = request.args.get("format", "csv").strip()
        sender = request.args.get("sender", "").strip()
        date_from = request.args.get("from", "").strip()
        date_to = request.args.get("to", "").strip()
        search_type = request.args.get("type", "all").strip()

        if not q:
            abort(400, "Missing query")

        # Get all results (no pagination limit)
        if chat_name == '__all__' or not chat_name:
            all_results = []
            for name in sorted(os.listdir(chats_dir)):
                chat_dir_path = os.path.join(chats_dir, name)
                db_path = os.path.join(chat_dir_path, "data", "chat.db")
                if not os.path.isdir(chat_dir_path) or not os.path.exists(db_path):
                    continue
                try:
                    results, _ = indexer.search(db_path, q, sender=sender, date_from=date_from, date_to=date_to, page=1, per_page=1000, search_type=search_type)
                    for r in results:
                        r["chat_name"] = name
                    all_results.extend(results)
                except Exception:
                    continue
            results = all_results
        else:
            _, db_path = get_chat_paths(chat_name)
            results, _ = indexer.search(db_path, q, sender=sender, date_from=date_from, date_to=date_to, page=1, per_page=5000, search_type=search_type)

        if fmt == 'json':
            import json
            output = json.dumps(results, ensure_ascii=False, indent=2)
            return Response(output, mimetype='application/json',
                           headers={'Content-Disposition': 'attachment; filename=whatsarch_export.json'})
        else:
            import csv, io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['id', 'datetime', 'sender', 'text', 'transcription', 'visual_description', 'media_type', 'chat_name'])
            for r in results:
                writer.writerow([
                    r.get('id', ''),
                    r.get('datetime', ''),
                    r.get('sender', ''),
                    r.get('text', ''),
                    r.get('transcription', ''),
                    r.get('visual_description', ''),
                    r.get('media_type', ''),
                    r.get('chat_name', chat_name or ''),
                ])
            csv_content = output.getvalue()
            # Add BOM for Hebrew Excel compatibility
            return Response('\ufeff' + csv_content, mimetype='text/csv; charset=utf-8',
                           headers={'Content-Disposition': 'attachment; filename=whatsarch_export.csv'})

    # ------------------------------------------------------------------
    # Media serving
    # ------------------------------------------------------------------

    @app.route("/media/<path:chat_and_file>")
    def serve_media(chat_and_file):
        # Format: chat_name/filename
        parts = chat_and_file.split("/", 1)
        if len(parts) != 2:
            abort(400, "Invalid media path")
        chat_name, filename = parts
        # Sanitize to prevent path traversal
        chat_name = os.path.basename(chat_name)
        filename = os.path.basename(filename)
        chat_dir = os.path.join(chats_dir, chat_name)
        if not os.path.isdir(chat_dir):
            abort(404)
        return send_from_directory(chat_dir, filename)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _convert_to_direct_url(url: str):
        """Convert cloud storage share links to direct download URLs."""
        import re
        # Google Drive: https://drive.google.com/file/d/FILE_ID/view?usp=sharing
        m = re.search(r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)', url)
        if m:
            return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

        # Google Drive: https://drive.google.com/open?id=FILE_ID
        m = re.search(r'drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)', url)
        if m:
            return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

        # Dropbox: replace dl=0 with dl=1
        if 'dropbox.com' in url:
            return url.replace('dl=0', 'dl=1').replace('www.dropbox.com', 'dl.dropboxusercontent.com')

        # OneDrive: replace 'redir' with 'download'
        if '1drv.ms' in url or 'onedrive.live.com' in url:
            if '?' in url:
                return url + '&download=1'
            return url + '?download=1'

        # Direct URL (already a direct link to a file)
        if url.lower().endswith('.zip'):
            return url

        return None

    def _get_chunk_count(db_path):
        """Get the number of chunks in the database."""
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM chunks")
            return c.fetchone()[0]
        except Exception:
            return 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Settings endpoints
    # ------------------------------------------------------------------

    @app.route("/api/settings")
    @require_auth
    def api_settings():
        """Get current settings including API key status and model preferences."""
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)

        # For admin or local mode: show system keys
        # For regular users: show only their own keys
        user = get_current_user()
        is_admin = user and user.get("email") == ADMIN_EMAIL

        if is_admin or not _is_web_mode():
            keys = config.get_api_keys()
        else:
            # Regular user - show only their own keys (stored per-user)
            user_keys = get_user_api_keys(user["email"]) if user else {}
            keys = {
                "anthropic_key": user_keys.get("anthropic_key", ""),
                "openai_key": user_keys.get("openai_key", ""),
                "gemini_key": user_keys.get("gemini_key", ""),
            }

        return jsonify({
            "settings": settings,
            "api_keys": {
                "anthropic_configured": bool(keys.get("anthropic_key")),
                "anthropic_preview": keys["anthropic_key"][:8] + "..." if len(keys.get("anthropic_key", "")) > 8 else "",
                "openai_configured": bool(keys.get("openai_key")),
                "openai_preview": keys["openai_key"][:8] + "..." if len(keys.get("openai_key", "")) > 8 else "",
                "gemini_configured": bool(keys.get("gemini_key")),
                "gemini_preview": keys["gemini_key"][:8] + "..." if len(keys.get("gemini_key", "")) > 8 else "",
                "is_system_keys": is_admin or not _is_web_mode(),
            },
        })

    @app.route("/api/settings", methods=["POST"])
    @require_auth
    def api_settings_update():
        """Update settings and/or API keys."""
        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        project_root = os.path.dirname(chats_dir)

        # Update API keys if provided
        key_fields = {}
        for field in ("anthropic_key", "openai_key", "gemini_key"):
            if field in data:
                key_fields[field] = data[field]
        if key_fields:
            user = get_current_user()
            is_admin = user and user.get("email") == ADMIN_EMAIL
            if is_admin or not _is_web_mode():
                # Admin: save as system keys
                config.save_api_keys(project_root, key_fields)
            elif user:
                # Regular user: save as their own personal keys
                settings = config.load_settings(project_root)
                if "user_api_keys" not in settings:
                    settings["user_api_keys"] = {}
                if user["email"] not in settings["user_api_keys"]:
                    settings["user_api_keys"][user["email"]] = {}
                settings["user_api_keys"][user["email"]].update(key_fields)
                config.save_settings(project_root, settings)

        # Update model/provider settings
        setting_fields = {}
        valid_keys = set(config.DEFAULT_SETTINGS.keys())
        for k, v in data.items():
            if k in valid_keys:
                setting_fields[k] = v
        if setting_fields:
            config.update_settings(project_root, setting_fields)

        # Reset AI client so it picks up new settings
        ai_chat._llm_client = None
        ai_chat._llm_client_key = None

        return jsonify({"status": "ok"})

    @app.route("/api/user/storage")
    @require_auth
    def api_user_storage():
        """Get user's cloud storage usage."""
        user = getattr(request, 'user', None)
        if not user or not _is_web_mode():
            return jsonify({"total_bytes": 0, "chats": [], "quota_bytes": 0})
        sb = _get_supabase_client()
        if not sb:
            return jsonify({"total_bytes": 0, "chats": [], "quota_bytes": 0})
        try:
            from . import storage
            usage = storage.get_user_storage_usage(sb, user["id"])
            plan = get_user_plan(user["email"]).get("cloud_preset", "budget")
            quota = storage.STORAGE_QUOTAS.get(plan, storage.STORAGE_QUOTAS["budget"])
            usage["quota_bytes"] = quota
            usage["plan"] = plan
            return jsonify(usage)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/hardware")
    def api_hardware():
        """Get hardware info and Ollama performance estimates."""
        hw = config.detect_hardware()
        perf = config.estimate_ollama_performance(hw)
        return jsonify({"hardware": hw, "performance": perf})

    @app.route("/api/models")
    def api_models():
        """Get available model options for each task, with lock status per user."""
        user = get_current_user()
        allowed = get_allowed_providers_for_user(user)
        filtered = config.filter_models_by_tier(config.PROVIDER_MODELS, allowed)
        return jsonify(filtered)

    @app.route("/api/presets")
    def api_presets():
        """Get preset packages with cost estimates for a chat."""
        chat_name = request.args.get("chat", "").strip()

        # Get media counts
        image_count = 0
        video_count = 0
        if chat_name:
            chat_dir = os.path.join(chats_dir, chat_name)
            if os.path.isdir(chat_dir):
                from . import process_manager
                try:
                    scan = process_manager.scan_chat_files(chat_dir)
                    image_count = len(scan.get("images", []))
                    video_count = len(scan.get("videos", []))
                except Exception:
                    pass

        # Get hardware for recommendation
        hw = config.detect_hardware()
        recommended = config.recommend_preset(image_count, video_count, hw)

        # Build preset list with costs
        presets = []
        for key, preset in config.PRESETS.items():
            costs = config.estimate_preset_cost(key, image_count, video_count)
            presets.append({
                "key": key,
                "icon": preset["icon"],
                "name_he": preset["name_he"],
                "name_en": preset["name_en"],
                "description_he": preset["description_he"],
                "description_en": preset["description_en"],
                "costs": costs,
                "recommended": key == recommended,
            })

        return jsonify({
            "presets": presets,
            "chat_name": chat_name,
            "image_count": image_count,
            "video_count": video_count,
            "recommended": recommended,
        })

    # ------------------------------------------------------------------
    # Admin: User Management
    # ------------------------------------------------------------------

    @app.route("/api/admin/users")
    @require_admin
    def api_admin_users():
        """List all users with their plans. Admin only."""
        sb = _get_supabase_client()
        if not sb:
            return jsonify({"users": []})

        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        user_plans = settings.get("user_plans", {})

        try:
            users_response = sb.auth.admin.list_users()
            users = []
            for u in users_response:
                email = u.email or ""
                user_meta = u.user_metadata or {}
                plan = config.normalize_user_plan(user_plans.get(email))
                tier = plan.get("tier", "free")
                tier_info = config.TIERS.get(tier, config.TIERS["free"])
                users.append({
                    "id": u.id,
                    "email": email,
                    "display_name": user_meta.get("display_name", email.split("@")[0]),
                    "plan": plan,
                    "tier": tier,
                    "tier_name_he": tier_info["name_he"],
                    "tier_name_en": tier_info["name_en"],
                    "is_admin": email == ADMIN_EMAIL,
                    "created_at": str(u.created_at) if u.created_at else "",
                    "last_sign_in": str(u.last_sign_in_at) if u.last_sign_in_at else "",
                })
            return jsonify({"users": users})
        except Exception as e:
            return jsonify({"users": [], "error": str(e)})

    @app.route("/api/admin/users", methods=["POST"])
    @require_admin
    def api_admin_update_user():
        """Update a user's plan (partial update). Admin only."""
        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        email = data.get("email", "").strip()
        if not email:
            abort(400, "Missing email")

        # Validate provided fields
        tier = data.get("tier", "").strip()
        mode = data.get("mode", "").strip()

        if tier and tier not in config.VALID_TIERS:
            abort(400, f"Invalid tier: {tier}. Valid: {', '.join(config.VALID_TIERS)}")
        if mode and mode not in config.VALID_MODES:
            abort(400, f"Invalid mode: {mode}")

        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        if "user_plans" not in settings:
            settings["user_plans"] = {}

        # Load existing plan, then update only provided fields
        existing = config.normalize_user_plan(settings["user_plans"].get(email))
        if tier:
            existing["tier"] = tier
        if mode:
            existing["mode"] = mode

        settings["user_plans"][email] = existing
        config.save_settings(project_root, settings)

        return jsonify({"status": "ok", "email": email, "plan": existing})

    @app.route("/api/admin/users", methods=["DELETE"])
    @require_admin
    def api_admin_delete_user():
        """Remove a user's plan (reset to default). Admin only."""
        data = request.get_json()
        if not data:
            abort(400)
        email = data.get("email", "").strip()
        if not email:
            abort(400)

        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        user_plans = settings.get("user_plans", {})
        user_plans.pop(email, None)
        settings["user_plans"] = user_plans
        config.save_settings(project_root, settings)

        return jsonify({"status": "ok", "email": email, "plan": config.DEFAULT_USER_PLAN})

    @app.route("/api/user/plan")
    @require_auth
    def api_user_plan():
        """Get the current user's assigned plan with tier info."""
        user = request.user
        plan = get_user_plan(user["email"])
        tier = plan.get("tier", "free")
        tier_info = config.TIERS.get(tier, config.TIERS["free"])
        allowed = get_allowed_providers_for_user(user)
        user_keys = get_user_api_keys(user["email"])
        return jsonify({
            "tier": tier,
            "tier_name_he": tier_info["name_he"],
            "tier_name_en": tier_info["name_en"],
            "tier_description_he": tier_info["description_he"],
            "tier_description_en": tier_info["description_en"],
            "allowed_providers": sorted(allowed),
            "has_own_keys": {
                "gemini": bool(user_keys.get("gemini_key")),
                "openai": bool(user_keys.get("openai_key")),
                "anthropic": bool(user_keys.get("anthropic_key")),
            },
            "is_admin": user["email"] == ADMIN_EMAIL,
            "mode": plan.get("mode", "cloud"),
        })

    @app.route("/api/aliases")
    @require_auth
    def api_aliases():
        """Get sender aliases for a chat."""
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        aliases = settings.get("sender_aliases", {}).get(chat_name, {})

        # Also return the list of actual senders from stats
        _, db_path = get_chat_paths(chat_name)
        stats = indexer.get_stats(db_path)
        senders = list(stats.get("senders", {}).keys())

        return jsonify({"chat": chat_name, "senders": senders, "aliases": aliases})

    @app.route("/api/aliases", methods=["POST"])
    @require_auth
    def api_aliases_update():
        """Update sender aliases for a chat. Applied on next index rebuild."""
        data = request.get_json()
        if not data:
            abort(400, "Missing JSON body")

        chat_name = data.get("chat", "").strip()
        aliases = data.get("aliases", {})

        if not chat_name:
            abort(400, "Missing chat name")

        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        if "sender_aliases" not in settings:
            settings["sender_aliases"] = {}
        settings["sender_aliases"][chat_name] = aliases
        config.save_settings(project_root, settings)

        return jsonify({"status": "ok", "note": "Aliases saved. Run 'Update Search' to apply."})

    # ------------------------------------------------------------------
    # Proxy endpoints (for local agent to use admin's API keys)
    # ------------------------------------------------------------------

    @app.route("/api/proxy/vision", methods=["POST"])
    @require_auth
    def api_proxy_vision():
        """Proxy endpoint: agent sends base64 image, server calls Vision API with admin keys.

        Accepts: {image_base64, media_type, language, provider_override, model_override}
        Returns: {description}
        """
        data = request.get_json()
        if not data or not data.get("image_base64"):
            abort(400, "Missing image_base64")

        image_b64 = data["image_base64"]
        media_type = data.get("media_type", "image/jpeg")
        language = data.get("language", "he")

        # Use admin's configured settings (enforce user preset)
        enforce_user_tier()
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        provider = settings.get("vision_provider", "gemini")
        model = settings.get("vision_model", "gemini-2.5-flash")

        from . import vision
        from . import process_manager as pm

        api_key = pm._get_api_key_for_provider(provider)
        ollama_url = settings.get("ollama_base_url")

        try:
            description = vision.describe_image_from_base64(
                image_b64, media_type, provider=provider, model=model,
                api_key=api_key, ollama_url=ollama_url, language=language
            )
            return jsonify({"description": description, "provider": provider})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/proxy/rag", methods=["POST"])
    @require_auth
    def api_proxy_rag():
        """Proxy endpoint: agent sends question + context, server calls RAG API with admin keys.

        Accepts: {question, context, chat_name, language, history}
        Returns: {answer, provider}
        """
        data = request.get_json()
        if not data or not data.get("question"):
            abort(400, "Missing question")

        question = data["question"]
        context_text = data.get("context", "")
        chat_name = data.get("chat_name", "chat")
        language = data.get("language", "he")
        history = data.get("history", [])

        # Use admin's configured settings (enforce user preset)
        enforce_user_tier()
        project_root = os.path.dirname(chats_dir)

        try:
            result = ai_chat.ask_with_context(
                context_text, question, chat_name, history,
                project_root=project_root, language=language
            )
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ------------------------------------------------------------------
    # File upload (ZIP) endpoint
    # ------------------------------------------------------------------

    @app.route("/api/upload", methods=["POST"])
    @require_auth
    def api_upload():
        """Upload a WhatsApp/Telegram export ZIP file for processing.

        In web mode: saves to user's directory, starts background processing.
        In local mode: saves to chats/ directory.
        """
        import zipfile
        import shutil
        import tempfile
        from . import process_manager

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "Empty filename"}), 400

        # Optional chat name override
        chat_name = request.form.get("chat_name", "").strip()

        # Save uploaded file to temp
        temp_dir = tempfile.mkdtemp()
        temp_zip = os.path.join(temp_dir, "upload.zip")
        try:
            file.save(temp_zip)
            file_size_mb = os.path.getsize(temp_zip) / (1024 * 1024)

            # Extract ZIP
            if not zipfile.is_zipfile(temp_zip):
                return jsonify({"error": "File is not a valid ZIP archive"}), 400

            extract_dir = os.path.join(temp_dir, "extracted")
            with zipfile.ZipFile(temp_zip, "r") as zf:
                # Fix Hebrew/Unicode filenames (ZIP uses cp437 by default)
                for info in zf.infolist():
                    try:
                        info.filename = info.filename.encode('cp437').decode('utf-8')
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass
                zf.extractall(extract_dir)

            # Find the chat content (might be in a subfolder)
            from .parser import detect_platform
            chat_root = extract_dir

            # Check if content is in a subfolder
            entries = os.listdir(extract_dir)
            if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
                subfolder = os.path.join(extract_dir, entries[0])
                if detect_platform(subfolder) != "unknown":
                    chat_root = subfolder

            platform = detect_platform(chat_root)
            if platform == "unknown":
                return jsonify({"error": "Could not find WhatsApp (_chat.txt) or Telegram (result.json) export in ZIP"}), 400

            # Determine chat name
            if not chat_name:
                if len(entries) == 1:
                    chat_name = entries[0]
                else:
                    # Try to decode filename properly
                    raw_name = file.filename or "uploaded_chat"
                    try:
                        raw_name = raw_name.encode('latin-1').decode('utf-8')
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass
                    chat_name = os.path.splitext(raw_name)[0]
            # Sanitize
            chat_name = "".join(c for c in chat_name if c not in '<>:"/\\|?*').strip()
            if not chat_name:
                chat_name = "uploaded_chat"

            # Move to chats directory
            dest_dir = os.path.join(chats_dir, chat_name)
            if os.path.exists(dest_dir):
                # Append number to avoid collision
                i = 2
                while os.path.exists(f"{dest_dir}_{i}"):
                    i += 1
                chat_name = f"{chat_name}_{i}"
                dest_dir = os.path.join(chats_dir, chat_name)

            shutil.copytree(chat_root, dest_dir)

            # Register in Supabase if web mode + upload to cloud storage
            user = getattr(request, "user", None)
            if user and _is_web_mode():
                sb = _get_supabase_client()
                if sb:
                    # Upload to Supabase Storage for persistence
                    try:
                        from . import storage
                        storage_result = storage.upload_chat_data(sb, user["id"], chat_name, dest_dir)
                    except Exception as e:
                        print(f"[Storage] Upload warning: {e}")
                        storage_result = {}

                    try:
                        sb.table("user_chats").insert({
                            "user_id": user["id"],
                            "chat_name": chat_name,
                            "platform": platform,
                            "status": "uploaded",
                            "file_size_mb": round(file_size_mb, 2),
                            "storage_path": dest_dir,
                        }).execute()
                    except Exception as e:
                        print(f"Supabase insert error: {e}")

            return jsonify({
                "status": "ok",
                "chat_name": chat_name,
                "platform": platform,
                "file_size_mb": round(file_size_mb, 2),
                "message": f"Chat '{chat_name}' uploaded successfully. Use the Management tab to start processing.",
            })

        except zipfile.BadZipFile:
            return jsonify({"error": "Corrupted ZIP file"}), 400
        except Exception as e:
            return jsonify({"error": f"Upload failed: {str(e)}"}), 500
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @app.route("/api/upload/url", methods=["POST"])
    @require_auth
    def api_upload_url():
        """Download a ZIP from a shared URL (Google Drive, Dropbox, OneDrive).

        Accepts: {"url": "https://drive.google.com/..."} or similar share links.
        Downloads to temp, then processes like a regular upload.
        """
        import zipfile
        import shutil
        import tempfile
        import re
        import urllib.request
        from . import process_manager

        data = request.get_json()
        if not data or not data.get("url", "").strip():
            return jsonify({"error": "Missing URL"}), 400

        url = data["url"].strip()

        # Convert share links to direct download URLs
        direct_url = _convert_to_direct_url(url)
        if not direct_url:
            return jsonify({"error": "Unsupported URL. Use Google Drive, Dropbox, or OneDrive share links."}), 400

        temp_dir = tempfile.mkdtemp()
        try:
            temp_zip = os.path.join(temp_dir, "download.zip")

            # Download the file
            req = urllib.request.Request(direct_url, headers={"User-Agent": "WhatsArch/1.0"})
            with urllib.request.urlopen(req, timeout=300) as response:
                with open(temp_zip, "wb") as f:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)

            file_size_mb = os.path.getsize(temp_zip) / (1024 * 1024)

            # Verify it's a valid ZIP
            if not zipfile.is_zipfile(temp_zip):
                return jsonify({"error": "Downloaded file is not a valid ZIP"}), 400

            # Extract and detect platform
            extract_dir = os.path.join(temp_dir, "extracted")
            with zipfile.ZipFile(temp_zip, "r") as zf:
                zf.extractall(extract_dir)

            # Find the chat content (same logic as regular upload)
            from . import parser
            chat_root = extract_dir

            # Check if files are in a subdirectory
            entries = os.listdir(extract_dir)
            if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
                chat_root = os.path.join(extract_dir, entries[0])

            platform = parser.detect_platform(chat_root)
            if not platform:
                return jsonify({"error": "No WhatsApp (_chat.txt) or Telegram (result.json) export found in ZIP"}), 400

            # Determine chat name
            if platform == "whatsapp":
                chat_name = os.path.basename(chat_root)
                if chat_name == "extracted":
                    chat_name = "uploaded_chat"
            else:
                chat_name = os.path.basename(chat_root)
                if chat_name == "extracted":
                    chat_name = "telegram_chat"

            # Clean up name
            chat_name = re.sub(r'[<>:"/\\|?*]', '_', chat_name)

            # Move to chats directory
            dest = os.path.join(chats_dir, chat_name)
            if os.path.exists(dest):
                chat_name = chat_name + "_" + str(int(os.path.getmtime(temp_zip)))
                dest = os.path.join(chats_dir, chat_name)

            shutil.move(chat_root, dest)
            os.makedirs(os.path.join(dest, "data"), exist_ok=True)

            return jsonify({
                "status": "ok",
                "chat_name": chat_name,
                "platform": platform,
                "file_size_mb": round(file_size_mb, 2),
                "message": f"Chat '{chat_name}' downloaded and extracted. Use the Management tab to start processing.",
            })

        except urllib.error.URLError as e:
            return jsonify({"error": f"Download failed: {str(e)}"}), 400
        except zipfile.BadZipFile:
            return jsonify({"error": "Downloaded file is not a valid ZIP"}), 400
        except Exception as e:
            return jsonify({"error": f"Failed: {str(e)}"}), 500
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @app.route("/api/upload/share", methods=["POST"])
    @require_auth
    def api_upload_share():
        """PWA Share Target endpoint - receives shared files from mobile."""
        # Same as upload but handles multipart from share target
        if "file" in request.files:
            return api_upload()
        # Share target may send as 'shared-file'
        if "shared-file" in request.files:
            request.files["file"] = request.files["shared-file"]
            return api_upload()
        return jsonify({"error": "No file received"}), 400

    # ------------------------------------------------------------------
    # Security headers
    # ------------------------------------------------------------------

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    return app
