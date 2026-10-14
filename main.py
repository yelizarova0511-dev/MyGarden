
from flask import Flask

from app.routes import main_bp

app = Flask(__name__)

app.config["SECRET_KEY"] = "dev-key-change-before-production"

app.register_blueprint(main_bp)


if __name__ == "__main__":
    app.run(debug=True)
