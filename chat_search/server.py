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
                app._supabase = create_client(url, key)
            else:
                app._supabase = None
        return app._supabase

    def _is_web_mode():
        """Check if running in web/SaaS mode (Supabase configured)."""
        return bool(os.environ.get("SUPABASE_URL"))

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
        return render_template("index.html")

    @app.route("/login")
    def login_page():
        """Serve login/signup page (web mode only)."""
        if not _is_web_mode():
            return render_template("index.html")  # Local mode - skip auth
        return render_template("auth.html")

    @app.route("/auth/callback")
    def auth_callback():
        """OAuth callback - handles both PKCE (code in query) and implicit (token in hash)."""
        code = request.args.get("code")
        if code:
            # PKCE flow: exchange code for session server-side
            sb = _get_supabase_client()
            if sb:
                try:
                    result = sb.auth.exchange_code_for_session({"auth_code": code})
                    if result.session:
                        # Return page that stores tokens and redirects
                        return f"""<!DOCTYPE html><html><head><title>WhatsArch</title></head><body>
                        <script>
                        localStorage.setItem('auth_token', '{result.session.access_token}');
                        localStorage.setItem('refresh_token', '{result.session.refresh_token}');
                        localStorage.setItem('user_email', '{result.user.email}');
                        window.location.href = '/';
                        </script></body></html>"""
                except Exception as e:
                    return f"""<!DOCTYPE html><html><body>
                    <p>Authentication error: {str(e)}</p>
                    <a href="/login">Try again</a></body></html>"""

        # Implicit flow fallback: token in URL hash
        return """<!DOCTYPE html><html><head><title>WhatsArch</title></head><body>
        <script>
        const hash = window.location.hash.substring(1);
        const params = new URLSearchParams(hash);
        const token = params.get('access_token');
        const refresh = params.get('refresh_token');
        if (token) {
            localStorage.setItem('auth_token', token);
            if (refresh) localStorage.setItem('refresh_token', refresh);
            window.location.href = '/';
        } else {
            document.body.innerHTML = '<p>Authentication failed. <a href="/login">Try again</a></p>';
        }
        </script></body></html>"""

    @app.route("/api/chats")
    def api_chats():
        """List all available chats with their status (WhatsApp + Telegram)."""
        result = []
        for name in sorted(os.listdir(chats_dir)):
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
    def api_search_all():
        """Search across all chats simultaneously."""
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"results": [], "total": 0})

        sender = request.args.get("sender", "").strip()
        date_from = request.args.get("from", "").strip()
        date_to = request.args.get("to", "").strip()
        search_type = request.args.get("type", "all").strip()
        page = int(request.args.get("page", 1))

        all_results = []

        # Search each chat
        for name in sorted(os.listdir(chats_dir)):
            chat_dir = os.path.join(chats_dir, name)
            db_path = os.path.join(chat_dir, "data", "chat.db")
            if not os.path.isdir(chat_dir) or not os.path.exists(db_path):
                continue

            try:
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
        page = int(request.args.get("page", 1))

        if not q:
            return jsonify({"results": [], "total": 0, "page": 1})

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
    def api_context(message_id):
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        _, db_path = get_chat_paths(chat_name)

        before = int(request.args.get("before", 5))
        after = int(request.args.get("after", 5))
        messages = indexer.get_context(db_path, message_id, before, after)
        return jsonify({"messages": messages, "focus_id": message_id})

    @app.route("/api/stats")
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
    def api_ai_status():
        """Check AI provider availability."""
        info = ai_chat.LLMClient.get_provider_info()
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        info["current_rag_provider"] = settings.get("rag_provider")
        info["current_rag_model"] = settings.get("rag_model")
        return jsonify(info)

    @app.route("/api/ai/chat", methods=["POST"])
    def api_ai_chat():
        """AI chat endpoint - RAG pipeline."""
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
    def api_ai_chat_stream():
        """Streaming AI chat endpoint using Server-Sent Events."""
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
    def api_process_progress():
        """Lightweight polling endpoint for active task progress."""
        chat_name = request.args.get("chat", "").strip()
        if not chat_name:
            abort(400, "Missing chat parameter")

        from . import process_manager
        task_info = process_manager.get_task_status(chat_name)
        return jsonify(task_info or {"status": "idle"})

    @app.route("/api/process/stop", methods=["POST"])
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
    def api_media_list():
        """List all media files for a chat with metadata."""
        chat_name = request.args.get("chat", "").strip()
        media_type_filter = request.args.get("type", "all").strip()  # all, image, video
        page = int(request.args.get("page", 1))
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
    # Export endpoint
    # ------------------------------------------------------------------

    @app.route("/api/export")
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
    def api_settings():
        """Get current settings including API key status and model preferences."""
        project_root = os.path.dirname(chats_dir)
        settings = config.load_settings(project_root)
        keys = config.get_api_keys()
        return jsonify({
            "settings": settings,
            "api_keys": {
                "anthropic_configured": bool(keys["anthropic_key"]),
                "anthropic_preview": keys["anthropic_key"][:8] + "..." if len(keys["anthropic_key"]) > 8 else "",
                "openai_configured": bool(keys["openai_key"]),
                "openai_preview": keys["openai_key"][:8] + "..." if len(keys["openai_key"]) > 8 else "",
                "gemini_configured": bool(keys["gemini_key"]),
                "gemini_preview": keys["gemini_key"][:8] + "..." if len(keys["gemini_key"]) > 8 else "",
            },
        })

    @app.route("/api/settings", methods=["POST"])
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
            config.save_api_keys(project_root, key_fields)

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

        return jsonify({"status": "ok"})

    @app.route("/api/hardware")
    def api_hardware():
        """Get hardware info and Ollama performance estimates."""
        hw = config.detect_hardware()
        perf = config.estimate_ollama_performance(hw)
        return jsonify({"hardware": hw, "performance": perf})

    @app.route("/api/models")
    def api_models():
        """Get available model options for each task."""
        return jsonify(config.PROVIDER_MODELS)

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
                    chat_name = os.path.splitext(file.filename)[0]
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

            # Register in Supabase if web mode
            user = getattr(request, "user", None)
            if user and _is_web_mode():
                sb = _get_supabase_client()
                if sb:
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

    return app
