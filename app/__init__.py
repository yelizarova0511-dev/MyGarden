from pathlib import Path

from flask import Flask


def create_app():
    app = Flask(
        __name__,
        template_folder=str(
            Path(__file__).resolve().parent.parent / "templates"
        ),
        static_folder=str(
            Path(__file__).resolve().parent.parent / "static"
        ),
    )

    app.config["SECRET_KEY"] = "dev-key-change-before-production"

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    return app