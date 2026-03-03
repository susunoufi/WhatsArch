"""Flask web server for multi-chat search UI."""

import os
import mimetypes
from flask import Flask, request, jsonify, render_template, send_from_directory, abort

from . import indexer, ai_chat

# Register .opus MIME type so browsers can play audio
mimetypes.add_type("audio/ogg", ".opus")


def create_app(chats_dir: str) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["CHATS_DIR"] = chats_dir

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

    @app.route("/api/chats")
    def api_chats():
        """List all available chats with their status."""
        result = []
        for name in sorted(os.listdir(chats_dir)):
            chat_dir = os.path.join(chats_dir, name)
            chat_file = os.path.join(chat_dir, "_chat.txt")
            db_path = os.path.join(chat_dir, "data", "chat.db")
            if os.path.isdir(chat_dir) and os.path.exists(chat_file):
                ready = os.path.exists(db_path)
                info = {"name": name, "ready": ready}
                if ready:
                    stats = indexer.get_stats(db_path)
                    info["total_messages"] = stats["total_messages"]
                result.append(info)
        return jsonify(result)

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
        """Check if AI chat is available."""
        info = ai_chat.LLMClient.get_provider_info()
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
            result = ai_chat.ask(db_path, question, chat_name, history)
            return jsonify(result)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503
        except Exception as e:
            return jsonify({"error": f"שגיאה: {str(e)}"}), 500

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

    return app
