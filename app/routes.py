import sqlite3
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

main_bp = Blueprint("main", __name__)

DATABASE = Path(__file__).resolve().parent.parent / "users.db"


def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA foreign_keys = OFF")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
            """
        )

        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(users)")
        }

        # Удаляем email и email_verified из старой таблицы.
        # ID, логины и хеши паролей сохраняются.
        if "email" in columns or "email_verified" in columns:
            connection.execute(
                """
                CREATE TABLE users_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
INSERT INTO users_new (id, username, password_hash)
SELECT id, username, password_hash
FROM users
"""
            )

            connection.execute("DROP TABLE users")
            connection.execute("ALTER TABLE users_new RENAME TO users")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS plots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                area REAL NOT NULL,
                description TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS plants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plot_id INTEGER,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                planting_date TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (plot_id) REFERENCES plots(id)
            )
            """
        )

        plant_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(plants)")
        }

        if "variety" not in plant_columns:
            connection.execute(
                """
                ALTER TABLE plants
                ADD COLUMN variety TEXT NOT NULL DEFAULT ''
                """
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


init_db()


@main_bp.route("/")
def index():
    return render_template("index.html")


@main_bp.route("/about")
def about():
    return render_template("about.html")


@main_bp.route("/care")
def care():
    return render_template("care.html")


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not username or not password or not password_confirm:
            flash("Заполни все поля.", "error")
            return render_template("register.html")

        if len(username) < 3 or len(username) > 80:
            flash(
                "Имя пользователя должно содержать от 3 до 80 символов.",
                "error",
            )
            return render_template("register.html")

        if len(password) < 8:
            flash("Пароль должен содержать не менее 8 символов.", "error")
            return render_template("register.html")

        if password != password_confirm:
            flash("Пароли не совпадают.", "error")
            return render_template("register.html")

        try:
            with get_db_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO users (username, password_hash)
                    VALUES (?, ?)
                    """,
                    (username, generate_password_hash(password)),
                )

            flash("Регистрация прошла успешно! Теперь войди.", "success")
            return redirect(url_for("main.login"))

        except sqlite3.IntegrityError:
            flash("Такое имя пользователя уже занято.", "error")

    return render_template("register.html")


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with get_db_connection() as connection:
            user = connection.execute(
                """
                SELECT id, username, password_hash
                FROM users
                WHERE username = ?
                """,
                (username,),
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]

            flash("Ты успешно вошла в аккаунт!", "success")
            return redirect(url_for("main.profile"))

        flash("Неверное имя пользователя или пароль.", "error")

    return render_template("login.html")


@main_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Ты вышла из аккаунта.", "success")
    return redirect(url_for("main.index"))


@main_bp.route("/profile")
def profile():
    if "user_id" not in session:
        flash("Сначала войди в аккаунт.", "error")
        return redirect(url_for("main.login"))

    return render_template("profile.html")


@main_bp.route("/account/delete", methods=["POST"])
def delete_account():
    user_id = session.get("user_id")

    if user_id is None:
        flash("Сначала войди в аккаунт.", "error")
        return redirect(url_for("main.login"))

    try:
        with get_db_connection() as connection:
            connection.execute(
                "DELETE FROM plants WHERE user_id = ?",
                (user_id,),
            )

            connection.execute(
                "DELETE FROM plots WHERE user_id = ?",
                (user_id,),
            )

            cursor = connection.execute(
                "DELETE FROM users WHERE id = ?",
                (user_id,),
            )

            if cursor.rowcount == 0:
                flash("Аккаунт не найден.", "error")
                return redirect(url_for("main.profile"))

        session.clear()
        flash("Аккаунт и все связанные данные удалены.", "success")
        return redirect(url_for("main.index"))

    except sqlite3.Error:
        current_app.logger.exception("Ошибка удаления аккаунта.")
        flash(
            "Не удалось удалить аккаунт. Попробуй ещё раз.",
            "error",
        )
        return redirect(url_for("main.profile"))


@main_bp.route("/plots", methods=["GET", "POST"])
def plots():
    if "user_id" not in session:
        flash("Сначала войди в аккаунт.", "error")
        return redirect(url_for("main.login"))

    user_id = session["user_id"]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        area_text = request.form.get("area", "").strip()
        description = request.form.get("description", "").strip()

        if not name or not area_text:
            flash("Укажи название и площадь участка.", "error")
            return redirect(url_for("main.plots"))

        if len(name) > 100 or len(description) > 1000:
            flash("Проверь длину введённых данных.", "error")
            return redirect(url_for("main.plots"))

        try:
            area = float(area_text.replace(",", "."))

            if not 0 < area <= 1000000:
                raise ValueError

        except ValueError:
            flash(
                "Площадь должна быть числом больше 0 "
                "и не более 1000000 м².",
                "error",
            )
            return redirect(url_for("main.plots"))

        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO plots (user_id, name, area, description)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, name, area, description),
            )

        flash("Участок успешно добавлен!", "success")
        return redirect(url_for("main.plots"))

    with get_db_connection() as connection:
        user_plots = connection.execute(
            """
            SELECT id, name, area, description, created_at
            FROM plots
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()

    return render_template("plots.html", plots=user_plots)


@main_bp.route("/plots/delete/<int:plot_id>", methods=["POST"])
def delete_plot(plot_id):
    if "user_id" not in session:
        flash("Сначала войди в аккаунт.", "error")
        return redirect(url_for("main.login"))

    user_id = session["user_id"]

    with get_db_connection() as connection:
        connection.execute(
            """
            UPDATE plants
            SET plot_id = NULL
            WHERE plot_id = ? AND user_id = ?
            """,
            (plot_id, user_id),
        )

        cursor = connection.execute(
            """
            DELETE FROM plots
            WHERE id = ? AND user_id = ?
            """,
            (plot_id, user_id),
        )

    if cursor.rowcount:
        flash("Участок удалён. Растения сохранены без участка.", "success")
    else:
        flash("Участок не найден.", "error")

    return redirect(url_for("main.plots"))


@main_bp.route("/plants", methods=["GET", "POST"])
def plants():
    if "user_id" not in session:
        flash("Сначала войди в аккаунт.", "error")
        return redirect(url_for("main.login"))

    user_id = session["user_id"]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        variety = request.form.get("variety", "").strip()
        planting_date = request.form.get("planting_date", "").strip()
        notes = request.form.get("notes", "").strip()
        plot_id_text = request.form.get("plot_id", "").strip()

        if not name or not category or not variety:
            flash("Укажи название растения, культуру и сорт.", "error")
            return redirect(url_for("main.plants"))

        if (
            len(name) > 100
            or len(category) > 50
            or len(variety) > 100
            or len(planting_date) > 20
            or len(notes) > 1000
        ):
            flash("Проверь длину введённых данных.", "error")
            return redirect(url_for("main.plants"))

        plot_id = None

        if plot_id_text:
            try:
                plot_id = int(plot_id_text)
            except ValueError:
                flash("Выбран некорректный участок.", "error")
                return redirect(url_for("main.plants"))

            with get_db_connection() as connection:
                plot = connection.execute(
                    """
                    SELECT id
                    FROM plots
                    WHERE id = ? AND user_id = ?
                    """,
                    (plot_id, user_id),
                ).fetchone()

            if plot is None:
                flash("Выбранный участок не найден.", "error")
                return redirect(url_for("main.plants"))

        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO plants (
                    user_id, plot_id, name, category, variety,
                    planting_date, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    plot_id,
                    name,
                    category,
                    variety,
                    planting_date,
                    notes,
                ),
            )

        flash("Растение успешно добавлено!", "success")
        return redirect(url_for("main.plants"))

    with get_db_connection() as connection:
        user_plots = connection.execute(
            """
            SELECT id, name
            FROM plots
            WHERE user_id = ?
            ORDER BY name
            """,
            (user_id,),
        ).fetchall()

        user_plants = connection.execute(
            """
            SELECT
                plants.id,
                plants.name,
                plants.category,
                plants.variety,
                plants.planting_date,
                plants.notes,
                plots.name AS plot_name
            FROM plants
            LEFT JOIN plots ON plants.plot_id = plots.id
            WHERE plants.user_id = ?
            ORDER BY plants.id DESC
            """,
            (user_id,),
        ).fetchall()

    return render_template(
        "plants.html",
        plants=user_plants,
        plots=user_plots,
    )


@main_bp.route("/plants/delete/<int:plant_id>", methods=["POST"])
def delete_plant(plant_id):
    if "user_id" not in session:
        flash("Сначала войди в аккаунт.", "error")
        return redirect(url_for("main.login"))

    with get_db_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM plants
            WHERE id = ? AND user_id = ?
            """,
            (plant_id, session["user_id"]),
        )

    if cursor.rowcount:
        flash("Растение удалено.", "success")
    else:
        flash("Растение не найдено.", "error")

    return redirect(url_for("main.plants"))


@main_bp.app_errorhandler(404)
def page_not_found(_error):
    return render_template("404.html"), 404