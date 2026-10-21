import sqlite3
from functools import wraps
from pathlib import Path
from math import isfinite

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)


main_bp = Blueprint("main", __name__)
DATABASE = Path(__file__).resolve().parent.parent / "users.db"


# =========================================================
# РОЛИ MY GARDEN
# =========================================================

ROLE_USER = "user"
ROLE_EMPLOYEE = "employee"
ROLE_ADMIN = "admin"

ROLES = {
    ROLE_USER: "Клиент My Garden",
    ROLE_EMPLOYEE: "Сотрудник My Garden",
    ROLE_ADMIN: "Администратор My Garden",
}


# =========================================================
# ЭТАПЫ РОСТА РАСТЕНИЙ
# =========================================================

GROWTH_STAGES = {
    "sprout": {
        "name": "Росток",
        "progress": 0,
    },
    "leaves": {
        "name": "Листья",
        "progress": 20,
    },
    "flowering": {
        "name": "Цветение",
        "progress": 40,
    },
    "fruiting": {
        "name": "Появление плодов",
        "progress": 60,
    },
    "ripening": {
        "name": "Созревание",
        "progress": 80,
    },
    "harvested": {
        "name": "Сбор урожая",
        "progress": 100,
    },
}


# =========================================================
# ТАРИФЫ И ЦЕНЫ СЕМЯН
# =========================================================

DEFAULT_TARIFFS = {
    "area_rate": 20.0,
    "materials_rate": 5.0,
    "water_rate": 3.0,
    "minimum_harvest_percent": 30.0,
}

DEFAULT_SEED_PRICES = {
    "томат": 2.0,
    "помидор": 2.0,
    "огурец": 1.5,
    "перец": 3.0,
    "капуста": 1.0,
    "морковь": 0.2,
    "свёкла": 0.3,
    "свекла": 0.3,
    "картофель": 15.0,
    "тыква": 5.0,
}


# =========================================================
# ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ
# =========================================================

def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_table_columns(connection, table_name):
    return {
        row["name"]
        for row in connection.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()
    }


def add_column_if_missing(
    connection,
    table_name,
    column_name,
    definition,
):
    columns = get_table_columns(connection, table_name)

    if column_name not in columns:
        connection.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} {definition}"
        )


# =========================================================
# СОЗДАНИЕ И ОБНОВЛЕНИЕ БАЗЫ ДАННЫХ
# =========================================================

def init_db():
    connection = get_db_connection()

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
        """)

        add_column_if_missing(
            connection,
            "users",
            "role",
            "TEXT NOT NULL DEFAULT 'user'",
        )

        connection.execute("""
            UPDATE users
            SET role = 'user'
            WHERE role IS NULL OR role = ''
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS plots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                area REAL,
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS plants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plot_id INTEGER,
                name TEXT NOT NULL,
                category TEXT,
                planting_date TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (plot_id)
                    REFERENCES plots(id) ON DELETE SET NULL
            )
        """)

        plant_columns = {
            "variety": "TEXT",
            "seed_count": "INTEGER DEFAULT 0",
            "harvest_min_kg": "REAL DEFAULT 0",
            "harvest_est_min_kg": "REAL DEFAULT 0",
            "harvest_est_max_kg": "REAL DEFAULT 0",
            "harvest_actual_kg": "REAL DEFAULT 0",
            "growth_stage": "TEXT DEFAULT 'sprout'",
        }

        for column, definition in plant_columns.items():
            add_column_if_missing(
                connection,
                "plants",
                column,
                definition,
            )

        connection.execute("""
            UPDATE plants
            SET growth_stage = 'sprout'
            WHERE growth_stage IS NULL OR growth_stage = ''
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS tariff_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value REAL NOT NULL
            )
        """)

        for key, value in DEFAULT_TARIFFS.items():
            connection.execute("""
                INSERT OR IGNORE INTO tariff_settings (
                    setting_key,
                    setting_value
                )
                VALUES (?, ?)
            """, (key, value))

        connection.execute("""
            CREATE TABLE IF NOT EXISTS seed_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                culture TEXT NOT NULL UNIQUE COLLATE NOCASE,
                price_per_seed REAL NOT NULL DEFAULT 0
                    CHECK(price_per_seed >= 0)
            )
        """)

        for culture, price in DEFAULT_SEED_PRICES.items():
            connection.execute("""
                INSERT OR IGNORE INTO seed_prices (
                    culture,
                    price_per_seed
                )
                VALUES (?, ?)
            """, (culture, price))

        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plot_id INTEGER,
                plot_name TEXT NOT NULL,
                area_m2 REAL NOT NULL,
                area_rate REAL NOT NULL,
                materials_rate REAL NOT NULL,
                water_rate REAL NOT NULL,
                seed_cost REAL NOT NULL,
                total_cost REAL NOT NULL,
                minimum_harvest_percent REAL NOT NULL,
                forecast_min_kg REAL NOT NULL DEFAULT 0,
                guaranteed_harvest_kg REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (plot_id)
                    REFERENCES plots(id) ON DELETE SET NULL
            )
        """)

        connection.commit()

    except sqlite3.Error:
        connection.rollback()
        raise

    finally:
        connection.close()


init_db()


# =========================================================
# ПРОВЕРКА АВТОРИЗАЦИИ И ПРАВ
# =========================================================

def login_required(view_function):
    @wraps(view_function)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")

        if not user_id:
            flash(
                "Сначала войдите в аккаунт.",
                "warning",
            )
            return redirect(url_for("main.login"))

        connection = get_db_connection()

        try:
            user = connection.execute("""
                SELECT id, username, role
                FROM users
                WHERE id = ?
            """, (user_id,)).fetchone()

        finally:
            connection.close()

        if user is None:
            session.clear()

            flash(
                "Аккаунт не найден. Войдите снова.",
                "error",
            )
            return redirect(url_for("main.login"))

        current_role = user["role"] or ROLE_USER

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = current_role

        return view_function(*args, **kwargs)

    return wrapped


def role_required(*allowed_roles):
    def decorator(view_function):
        @wraps(view_function)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") not in allowed_roles:
                flash(
                    "У вас недостаточно прав для этого действия.",
                    "error",
                )
                return redirect(url_for("main.profile"))

            return view_function(*args, **kwargs)

        return wrapped

    return decorator


def is_staff():
    return session.get("role") in (
        ROLE_EMPLOYEE,
        ROLE_ADMIN,
    )


def get_current_user_id():
    return session.get("user_id")


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def parse_kg(value):
    if value is None or str(value).strip() == "":
        return 0.0

    try:
        number = float(
            str(value).strip().replace(",", ".")
        )

        if not isfinite(number):
            return 0.0

        return max(number, 0.0)

    except (ValueError, TypeError):
        return 0.0


def parse_seed_count(value):
    try:
        return max(int(value or 0), 0)

    except (ValueError, TypeError):
        return 0


def parse_harvest_form(form):
    stage = form.get("growth_stage", "sprout")

    if stage not in GROWTH_STAGES:
        stage = "sprout"

    return {
        "seed_count": parse_seed_count(
            form.get("seed_count")
        ),
        "harvest_min_kg": parse_kg(
            form.get("harvest_min_kg")
        ),
        "harvest_est_min_kg": parse_kg(
            form.get("harvest_est_min_kg")
        ),
        "harvest_est_max_kg": parse_kg(
            form.get("harvest_est_max_kg")
        ),
        "harvest_actual_kg": parse_kg(
            form.get("harvest_actual_kg")
        ),
        "growth_stage": stage,
    }


def enrich_plants(plants):
    enriched = []

    for plant in plants:
        item = dict(plant)

        stage = item.get("growth_stage") or "sprout"

        if stage not in GROWTH_STAGES:
            stage = "sprout"

        item["growth_stage"] = stage
        item["growth_stage_label"] = (
            GROWTH_STAGES[stage]["name"]
        )
        item["growth_stage_name"] = (
            GROWTH_STAGES[stage]["name"]
        )
        item["growth_progress"] = (
            GROWTH_STAGES[stage]["progress"]
        )

        for field in (
            "seed_count",
            "harvest_min_kg",
            "harvest_est_min_kg",
            "harvest_est_max_kg",
            "harvest_actual_kg",
        ):
            item[field] = item.get(field) or 0

        enriched.append(item)

    return enriched


def get_plant_by_id(plant_id):
    connection = get_db_connection()

    try:
        return connection.execute("""
            SELECT
                plants.*,
                plots.name AS plot_name,
                users.username AS owner_username
            FROM plants
            LEFT JOIN plots
                ON plants.plot_id = plots.id
            LEFT JOIN users
                ON plants.user_id = users.id
            WHERE plants.id = ?
        """, (plant_id,)).fetchone()

    finally:
        connection.close()


def get_tariffs(connection):
    tariffs = dict(DEFAULT_TARIFFS)

    rows = connection.execute("""
        SELECT setting_key, setting_value
        FROM tariff_settings
    """).fetchall()

    for row in rows:
        tariffs[row["setting_key"]] = float(
            row["setting_value"]
        )

    return tariffs


# =========================================================
# РАСЧЁТ СТОИМОСТИ ЗАКАЗА
# =========================================================

def calculate_plot_order(connection, plot):
    area = parse_kg(plot["area"])

    if area <= 0:
        raise ValueError(
            "У участка должна быть указана площадь больше 0 м²."
        )

    tariffs = get_tariffs(connection)

    area_cost = area * tariffs["area_rate"]
    materials_cost = area * tariffs["materials_rate"]
    water_cost = area * tariffs["water_rate"]

    plants = connection.execute("""
        SELECT
            name,
            seed_count,
            harvest_min_kg,
            harvest_est_min_kg,
            harvest_est_max_kg
        FROM plants
        WHERE plot_id = ? AND user_id = ?
    """, (
        plot["id"],
        plot["user_id"],
    )).fetchall()

    seed_prices = {
        row["culture"].strip().casefold():
            float(row["price_per_seed"])
        for row in connection.execute("""
            SELECT culture, price_per_seed
            FROM seed_prices
        """).fetchall()
    }

    seed_cost = 0.0
    forecast_min_kg = 0.0
    missing_prices = []

    for plant in plants:
        count = parse_seed_count(
            plant["seed_count"]
        )
        culture = (
            plant["name"] or ""
        ).strip().casefold()

        if count > 0:
            if culture not in seed_prices:
                missing_prices.append(plant["name"])
            else:
                seed_cost += (
                    count * seed_prices[culture]
                )

        estimated_min = parse_kg(
            plant["harvest_est_min_kg"]
        )

        if estimated_min <= 0:
            estimated_min = parse_kg(
                plant["harvest_min_kg"]
            )

        forecast_min_kg += estimated_min

    total_cost = (
        area_cost
        + materials_cost
        + water_cost
        + seed_cost
    )

    percent = tariffs["minimum_harvest_percent"]
    guaranteed_kg = forecast_min_kg * percent / 100

    return {
        "area_m2": area,
        "area_rate": tariffs["area_rate"],
        "materials_rate": tariffs["materials_rate"],
        "water_rate": tariffs["water_rate"],
        "area_cost": area_cost,
        "materials_cost": materials_cost,
        "water_cost": water_cost,
        "seed_cost": seed_cost,
        "total_cost": total_cost,
        "minimum_harvest_percent": percent,
        "forecast_min_kg": forecast_min_kg,
        "guaranteed_harvest_kg": guaranteed_kg,
        "missing_seed_prices": sorted(
            set(missing_prices)
        ),
    }


# =========================================================
# ГЛАВНЫЕ СТРАНИЦЫ
# =========================================================

@main_bp.route("/")
def index():
    return render_template("index.html")


@main_bp.route("/about")
def about():
    return render_template("about.html")


@main_bp.route("/care")
def care():
    return render_template("care.html")


# =========================================================
# РЕГИСТРАЦИЯ
# =========================================================

@main_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get(
            "password", ""
        )

        if not username or not password:
            flash(
                "Заполните логин и пароль.",
                "error",
            )
            return render_template("register.html")

        if len(username) < 3:
            flash(
                "Логин должен содержать минимум 3 символа.",
                "error",
            )
            return render_template("register.html")

        if len(password) < 6:
            flash(
                "Пароль должен содержать минимум 6 символов.",
                "error",
            )
            return render_template("register.html")

        connection = get_db_connection()

        try:
            existing = connection.execute("""
                SELECT id
                FROM users
                WHERE username = ?
            """, (username,)).fetchone()

            if existing:
                flash(
                    "Такой логин уже занят.",
                    "error",
                )
                return render_template("register.html")

            connection.execute("""
                INSERT INTO users (
                    username,
                    password_hash,
                    role
                )
                VALUES (?, ?, ?)
            """, (
                username,
                generate_password_hash(password),
                ROLE_USER,
            ))

            connection.commit()

            flash(
                "Регистрация прошла успешно. Теперь войдите.",
                "success",
            )

            return redirect(url_for("main.login"))

        except sqlite3.IntegrityError:
            connection.rollback()

            flash(
                "Не удалось создать аккаунт. "
                "Попробуйте другой логин.",
                "error",
            )

        finally:
            connection.close()

    return render_template("register.html")


# =========================================================
# ВХОД
# =========================================================

@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get(
            "password", ""
        )

        connection = get_db_connection()

        try:
            user = connection.execute("""
                SELECT id, username, password_hash, role
                FROM users
                WHERE username = ?
            """, (username,)).fetchone()

        finally:
            connection.close()

        if user and check_password_hash(
            user["password_hash"],
            password,
        ):
            session.clear()

            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"] or ROLE_USER

            flash(
                "Вы успешно вошли в аккаунт.",
                "success",
            )

            return redirect(url_for("main.profile"))

        flash(
            "Неверный логин или пароль.",
            "error",
        )

    return render_template("login.html")


# =========================================================
# ВЫХОД
# =========================================================

@main_bp.route("/logout")
def logout():
    session.clear()

    flash(
        "Вы вышли из аккаунта.",
        "success",
    )

    return redirect(url_for("main.index"))


# =========================================================
# ЛИЧНЫЙ КАБИНЕТ
# =========================================================

@main_bp.route("/profile")
@login_required
def profile():
    connection = get_db_connection()

    try:
        user = connection.execute("""
            SELECT id, username, role
            FROM users
            WHERE id = ?
        """, (
            get_current_user_id(),
        )).fetchone()

        if user is None:
            session.clear()

            flash(
                "Аккаунт не найден. Войдите снова.",
                "error",
            )

            return redirect(url_for("main.login"))

        user_id = user["id"]

        # Счётчики для пользовательского профиля.
        stats = {
            "plots": connection.execute("""
                SELECT COUNT(*)
                FROM plots
                WHERE user_id = ?
            """, (user_id,)).fetchone()[0],

            "plants": connection.execute("""
                SELECT COUNT(*)
                FROM plants
                WHERE user_id = ?
            """, (user_id,)).fetchone()[0],

            "orders": connection.execute("""
                SELECT COUNT(*)
                FROM orders
                WHERE user_id = ?
            """, (user_id,)).fetchone()[0],

            "pending_orders": connection.execute("""
                SELECT COUNT(*)
                FROM orders
                WHERE user_id = ?
                  AND status = 'pending'
            """, (user_id,)).fetchone()[0],
        }

    finally:
        connection.close()

    current_role = user["role"] or ROLE_USER

    session["role"] = current_role
    session["username"] = user["username"]

    return render_template(
        "profile.html",
        user=user,
        role=current_role,
        role_label=ROLES.get(
            current_role,
            "Клиент My Garden",
        ),
        stats=stats,
    )


# =========================================================
# АДМИНИСТРАТИВНАЯ ПАНЕЛЬ
# =========================================================

@main_bp.route("/admin")
@role_required(ROLE_ADMIN)
def admin_panel():
    connection = get_db_connection()

    try:
        stats = {
            "users": connection.execute("""
                SELECT COUNT(*)
                FROM users
            """).fetchone()[0],

            "employees": connection.execute("""
                SELECT COUNT(*)
                FROM users
                WHERE role = ?
            """, (
                ROLE_EMPLOYEE,
            )).fetchone()[0],

            "plants": connection.execute("""
                SELECT COUNT(*)
                FROM plants
            """).fetchone()[0],

            "plots": connection.execute("""
                SELECT COUNT(*)
                FROM plots
            """).fetchone()[0],

            "orders": connection.execute("""
                SELECT COUNT(*)
                FROM orders
            """).fetchone()[0],

            "pending_orders": connection.execute("""
                SELECT COUNT(*)
                FROM orders
                WHERE status = 'pending'
            """).fetchone()[0],
        }

    finally:
        connection.close()

    return render_template(
        "admin_panel.html",
        stats=stats,
    )


# =========================================================
# УДАЛЕНИЕ АККАУНТА
# =========================================================

@main_bp.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    user_id = get_current_user_id()
    connection = get_db_connection()

    try:
        connection.execute(
            "DELETE FROM orders WHERE user_id = ?",
            (user_id,),
        )

        connection.execute(
            "DELETE FROM plants WHERE user_id = ?",
            (user_id,),
        )

        connection.execute(
            "DELETE FROM plots WHERE user_id = ?",
            (user_id,),
        )

        connection.execute(
            "DELETE FROM users WHERE id = ?",
            (user_id,),
        )

        connection.commit()

    except sqlite3.Error:
        connection.rollback()

        flash(
            "Не удалось удалить аккаунт. Попробуйте ещё раз.",
            "error",
        )

        return redirect(url_for("main.profile"))

    finally:
        connection.close()

    session.clear()

    flash(
        "Ваш аккаунт и связанные с ним данные удалены.",
        "success",
    )

    return redirect(url_for("main.index"))


# =========================================================
# УЧАСТКИ
# =========================================================

@main_bp.route("/plots", methods=["GET", "POST"])
@login_required
def plots():
    user_id = get_current_user_id()

    if request.method == "POST":
        name = request.form.get(
            "name", ""
        ).strip()

        area = parse_kg(
            request.form.get("area")
        )

        description = request.form.get(
            "description", ""
        ).strip()

        if not name:
            flash(
                "Введите название участка.",
                "error",
            )
            return redirect(url_for("main.plots"))

        if area <= 0:
            flash(
                "Укажите площадь участка больше 0 м².",
                "error",
            )
            return redirect(url_for("main.plots"))

        connection = get_db_connection()

        try:
            connection.execute("""
                INSERT INTO plots (
                    user_id,
                    name,
                    area,
                    description
                )
                VALUES (?, ?, ?, ?)
            """, (
                user_id,
                name,
                area,
                description,
            ))

            connection.commit()

            flash(
                "Участок добавлен.",
                "success",
            )

        except sqlite3.Error:
            connection.rollback()

            flash(
                "Не удалось добавить участок.",
                "error",
            )

        finally:
            connection.close()

        return redirect(url_for("main.plots"))

    connection = get_db_connection()

    try:
        user_plots = connection.execute("""
            SELECT *
            FROM plots
            WHERE user_id = ?
            ORDER BY id DESC
        """, (
            user_id,
        )).fetchall()

    finally:
        connection.close()

    return render_template(
        "plots.html",
        plots=user_plots,
    )


@main_bp.route(
    "/plots/delete/<int:plot_id>",
    methods=["POST"],
)
@login_required
def delete_plot(plot_id):
    user_id = get_current_user_id()
    connection = get_db_connection()

    try:
        plot = connection.execute("""
            SELECT id
            FROM plots
            WHERE id = ? AND user_id = ?
        """, (
            plot_id,
            user_id,
        )).fetchone()

        if plot is None:
            flash(
                "Участок не найден или у вас нет к нему доступа.",
                "error",
            )

            return redirect(url_for("main.plots"))

        connection.execute("""
            UPDATE plants
            SET plot_id = NULL
            WHERE plot_id = ? AND user_id = ?
        """, (
            plot_id,
            user_id,
        ))

        connection.execute("""
            DELETE FROM plots
            WHERE id = ? AND user_id = ?
        """, (
            plot_id,
            user_id,
        ))

        connection.commit()

        flash(
            "Участок удалён.",
            "success",
        )

    except sqlite3.Error:
        connection.rollback()

        flash(
            "Не удалось удалить участок.",
            "error",
        )

    finally:
        connection.close()

    return redirect(url_for("main.plots"))


# =========================================================
# РАСТЕНИЯ
# =========================================================

@main_bp.route("/plants", methods=["GET", "POST"])
@login_required
def plants():
    user_id = get_current_user_id()
    staff = is_staff()

    if request.method == "POST":
        name = request.form.get(
            "name", ""
        ).strip()

        category = request.form.get(
            "category", ""
        ).strip()

        variety = request.form.get(
            "variety", ""
        ).strip()

        planting_date = (
            request.form.get("planting_date") or None
        )

        notes = request.form.get(
            "notes", ""
        ).strip()

        plot_id_raw = request.form.get(
            "plot_id", ""
        ).strip()

        if not name:
            flash(
                "Введите название растения.",
                "error",
            )
            return redirect(url_for("main.plants"))

        connection = get_db_connection()

        try:
            owner_id = user_id
            plot_id = None

            if plot_id_raw:
                try:
                    requested_plot_id = int(plot_id_raw)

                except ValueError:
                    flash(
                        "Выбран некорректный участок.",
                        "error",
                    )
                    return redirect(url_for("main.plants"))

                if staff:
                    plot = connection.execute("""
                        SELECT id, user_id
                        FROM plots
                        WHERE id = ?
                    """, (
                        requested_plot_id,
                    )).fetchone()

                else:
                    plot = connection.execute("""
                        SELECT id, user_id
                        FROM plots
                        WHERE id = ? AND user_id = ?
                    """, (
                        requested_plot_id,
                        user_id,
                    )).fetchone()

                if plot is None:
                    flash(
                        "Участок не найден или у вас нет к нему доступа.",
                        "error",
                    )
                    return redirect(url_for("main.plants"))

                plot_id = plot["id"]
                owner_id = plot["user_id"]

            if staff:
                harvest_data = parse_harvest_form(
                    request.form
                )

            else:
                harvest_data = {
                    "seed_count": 0,
                    "harvest_min_kg": 0,
                    "harvest_est_min_kg": 0,
                    "harvest_est_max_kg": 0,
                    "harvest_actual_kg": 0,
                    "growth_stage": "sprout",
                }

            connection.execute("""
                INSERT INTO plants (
                    user_id,
                    plot_id,
                    name,
                    category,
                    variety,
                    planting_date,
                    notes,
                    seed_count,
                    harvest_min_kg,
                    harvest_est_min_kg,
                    harvest_est_max_kg,
                    harvest_actual_kg,
                    growth_stage
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                owner_id,
                plot_id,
                name,
                category,
                variety,
                planting_date,
                notes,
                harvest_data["seed_count"],
                harvest_data["harvest_min_kg"],
                harvest_data["harvest_est_min_kg"],
                harvest_data["harvest_est_max_kg"],
                harvest_data["harvest_actual_kg"],
                harvest_data["growth_stage"],
            ))

            connection.commit()

            flash(
                "Растение добавлено.",
                "success",
            )

        except sqlite3.Error:
            connection.rollback()

            flash(
                "Не удалось добавить растение.",
                "error",
            )

        finally:
            connection.close()

        return redirect(url_for("main.plants"))

    connection = get_db_connection()

    try:
        if staff:
            plant_rows = connection.execute("""
                SELECT
                    plants.*,
                    plots.name AS plot_name,
                    users.username AS owner_username
                FROM plants
                LEFT JOIN plots
                    ON plants.plot_id = plots.id
                LEFT JOIN users
                    ON plants.user_id = users.id
                ORDER BY plants.id DESC
            """).fetchall()

            available_plots = connection.execute("""
                SELECT
                    plots.*,
                    users.username AS owner_username
                FROM plots
                LEFT JOIN users
                    ON plots.user_id = users.id
                ORDER BY plots.id DESC
            """).fetchall()

        else:
            plant_rows = connection.execute("""
                SELECT
                    plants.*,
                    plots.name AS plot_name,
                    users.username AS owner_username
                FROM plants
                LEFT JOIN plots
                    ON plants.plot_id = plots.id
                LEFT JOIN users
                    ON plants.user_id = users.id
                WHERE plants.user_id = ?
                ORDER BY plants.id DESC
            """, (
                user_id,
            )).fetchall()

            available_plots = connection.execute("""
                SELECT *
                FROM plots
                WHERE user_id = ?
                ORDER BY id DESC
            """, (
                user_id,
            )).fetchall()

    finally:
        connection.close()

    return render_template(
        "plants.html",
        plants=enrich_plants(plant_rows),
        plots=available_plots,
        role=session.get("role", ROLE_USER),
        is_staff=staff,
        growth_stages=GROWTH_STAGES,
    )


@main_bp.route(
    "/plants/update/<int:plant_id>",
    methods=["POST"],
)
@role_required(ROLE_EMPLOYEE, ROLE_ADMIN)
def update_plant(plant_id):
    plant = get_plant_by_id(plant_id)

    if plant is None:
        flash(
            "Растение не найдено.",
            "error",
        )
        return redirect(url_for("main.plants"))

    harvest_data = parse_harvest_form(
        request.form
    )

    connection = get_db_connection()

    try:
        connection.execute("""
            UPDATE plants
            SET
                seed_count = ?,
                harvest_min_kg = ?,
                harvest_est_min_kg = ?,
                harvest_est_max_kg = ?,
                harvest_actual_kg = ?,
                growth_stage = ?
            WHERE id = ?
        """, (
            harvest_data["seed_count"],
            harvest_data["harvest_min_kg"],
            harvest_data["harvest_est_min_kg"],
            harvest_data["harvest_est_max_kg"],
            harvest_data["harvest_actual_kg"],
            harvest_data["growth_stage"],
            plant_id,
        ))

        connection.commit()

        flash(
            "Показатели растения обновлены.",
            "success",
        )

    except sqlite3.Error:
        connection.rollback()

        flash(
            "Не удалось обновить показатели растения.",
            "error",
        )

    finally:
        connection.close()

    return redirect(url_for("main.plants"))


@main_bp.route(
    "/plants/delete/<int:plant_id>",
    methods=["POST"],
)
@login_required
def delete_plant(plant_id):
    user_id = get_current_user_id()
    connection = get_db_connection()

    try:
        plant = connection.execute("""
            SELECT id
            FROM plants
            WHERE id = ? AND user_id = ?
        """, (
            plant_id,
            user_id,
        )).fetchone()

        if plant is None:
            flash(
                "Удалять можно только собственные растения.",
                "error",
            )

            return redirect(url_for("main.plants"))

        connection.execute("""
            DELETE FROM plants
            WHERE id = ? AND user_id = ?
        """, (
            plant_id,
            user_id,
        ))

        connection.commit()

        flash(
            "Растение удалено.",
            "success",
        )

    except sqlite3.Error:
        connection.rollback()

        flash(
            "Не удалось удалить растение.",
            "error",
        )

    finally:
        connection.close()

    return redirect(url_for("main.plants"))


# =========================================================
# ЗАКАЗЫ КЛИЕНТА MY GARDEN
# =========================================================

@main_bp.route("/orders", methods=["GET", "POST"])
@role_required(ROLE_USER)
def orders():
    user_id = get_current_user_id()
    connection = get_db_connection()

    try:
        if request.method == "POST":
            try:
                plot_id = int(
                    request.form.get("plot_id", "")
                )

            except (TypeError, ValueError):
                flash(
                    "Выберите участок.",
                    "error",
                )
                return redirect(url_for("main.orders"))

            plot = connection.execute("""
                SELECT id, user_id, name, area
                FROM plots
                WHERE id = ? AND user_id = ?
            """, (
                plot_id,
                user_id,
            )).fetchone()

            if plot is None:
                flash(
                    "Участок не найден или не принадлежит вам.",
                    "error",
                )
                return redirect(url_for("main.orders"))

            try:
                calculation = calculate_plot_order(
                    connection,
                    plot,
                )

            except ValueError as error:
                flash(str(error), "error")
                return redirect(url_for("main.orders"))

            if calculation["missing_seed_prices"]:
                names = ", ".join(
                    calculation["missing_seed_prices"]
                )

                flash(
                    "Не заданы цены семян для культур: "
                    + names
                    + ". Администратор должен настроить цены.",
                    "error",
                )

                return redirect(url_for("main.orders"))

            connection.execute("""
                INSERT INTO orders (
                    user_id,
                    plot_id,
                    plot_name,
                    area_m2,
                    area_rate,
                    materials_rate,
                    water_rate,
                    seed_cost,
                    total_cost,
                    minimum_harvest_percent,
                    forecast_min_kg,
                    guaranteed_harvest_kg,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                user_id,
                plot["id"],
                plot["name"],
                calculation["area_m2"],
                calculation["area_rate"],
                calculation["materials_rate"],
                calculation["water_rate"],
                calculation["seed_cost"],
                calculation["total_cost"],
                calculation["minimum_harvest_percent"],
                calculation["forecast_min_kg"],
                calculation["guaranteed_harvest_kg"],
            ))

            connection.commit()

            flash(
                "Заказ создан. Он ожидает оплаты. "
                "Онлайн-оплата пока не подключена.",
                "success",
            )

            return redirect(url_for("main.orders"))

        user_plots = connection.execute("""
            SELECT id, name, area
            FROM plots
            WHERE user_id = ?
            ORDER BY id DESC
        """, (
            user_id,
        )).fetchall()

        order_rows = connection.execute("""
            SELECT
                id,
                plot_name,
                area_m2,
                total_cost,
                minimum_harvest_percent,
                forecast_min_kg,
                guaranteed_harvest_kg,
                status,
                created_at
            FROM orders
            WHERE user_id = ?
            ORDER BY id DESC
        """, (
            user_id,
        )).fetchall()

        tariffs = get_tariffs(connection)

    finally:
        connection.close()

    status_labels = {
        "pending": "Ожидает оплаты",
        "paid": "Оплачен",
        "cancelled": "Отменён",
    }

    orders_list = []

    for order in order_rows:
        item = dict(order)

        item["status_label"] = status_labels.get(
            item["status"],
            item["status"],
        )

        orders_list.append(item)

    return render_template(
        "orders.html",
        plots=user_plots,
        orders=orders_list,
        tariffs=tariffs,
    )


@main_bp.route(
    "/orders/cancel/<int:order_id>",
    methods=["POST"],
)
@role_required(ROLE_USER)
def cancel_order(order_id):
    user_id = get_current_user_id()
    connection = get_db_connection()

    try:
        order = connection.execute("""
            SELECT id, status
            FROM orders
            WHERE id = ? AND user_id = ?
        """, (
            order_id,
            user_id,
        )).fetchone()

        if order is None:
            flash(
                "Заказ не найден.",
                "error",
            )

        elif order["status"] != "pending":
            flash(
                "Отменить можно только заказ, "
                "ожидающий оплаты.",
                "error",
            )

        else:
            connection.execute("""
                UPDATE orders
                SET status = 'cancelled'
                WHERE id = ?
                  AND user_id = ?
                  AND status = 'pending'
            """, (
                order_id,
                user_id,
            ))

            connection.commit()

            flash(
                "Заказ отменён.",
                "success",
            )

    except sqlite3.Error:
        connection.rollback()

        flash(
            "Не удалось отменить заказ.",
            "error",
        )

    finally:
        connection.close()

    return redirect(url_for("main.orders"))


# =========================================================
# ТАРИФЫ И ЦЕНЫ СЕМЯН
# ТОЛЬКО ДЛЯ АДМИНИСТРАТОРА
# =========================================================

@main_bp.route(
    "/admin/tariffs",
    methods=["GET", "POST"],
)
@role_required(ROLE_ADMIN)
def admin_tariffs():
    connection = get_db_connection()

    try:
        if request.method == "POST":
            action = request.form.get(
                "action",
                "tariffs",
            )

            if action == "tariffs":
                values = {
                    "area_rate": parse_kg(
                        request.form.get("area_rate")
                    ),
                    "materials_rate": parse_kg(
                        request.form.get("materials_rate")
                    ),
                    "water_rate": parse_kg(
                        request.form.get("water_rate")
                    ),
                    "minimum_harvest_percent": parse_kg(
                        request.form.get(
                            "minimum_harvest_percent"
                        )
                    ),
                }

                if values["minimum_harvest_percent"] > 100:
                    flash(
                        "Процент урожая должен быть "
                        "от 0 до 100.",
                        "error",
                    )

                    return redirect(
                        url_for("main.admin_tariffs")
                    )

                for key, value in values.items():
                    connection.execute("""
                        INSERT INTO tariff_settings (
                            setting_key,
                            setting_value
                        )
                        VALUES (?, ?)
                        ON CONFLICT(setting_key)
                        DO UPDATE SET
                            setting_value = excluded.setting_value
                    """, (
                        key,
                        value,
                    ))

                connection.commit()

                flash(
                    "Тарифы сохранены.",
                    "success",
                )

                return redirect(
                    url_for("main.admin_tariffs")
                )

            elif action == "seed_price":
                culture = request.form.get(
                    "culture", ""
                ).strip()

                price = parse_kg(
                    request.form.get("price_per_seed")
                )

                if not culture:
                    flash(
                        "Введите название культуры.",
                        "error",
                    )

                    return redirect(
                        url_for("main.admin_tariffs")
                    )

                connection.execute("""
                    INSERT INTO seed_prices (
                        culture,
                        price_per_seed
                    )
                    VALUES (?, ?)
                    ON CONFLICT(culture)
                    DO UPDATE SET
                        price_per_seed = excluded.price_per_seed
                """, (
                    culture,
                    price,
                ))

                connection.commit()

                flash(
                    "Цена семян сохранена.",
                    "success",
                )

                return redirect(
                    url_for("main.admin_tariffs")
                )

            elif action == "delete_seed_price":
                try:
                    price_id = int(
                        request.form.get("price_id", "")
                    )

                except (TypeError, ValueError):
                    flash(
                        "Некорректная запись цены.",
                        "error",
                    )

                    return redirect(
                        url_for("main.admin_tariffs")
                    )

                connection.execute(
                    "DELETE FROM seed_prices WHERE id = ?",
                    (price_id,),
                )

                connection.commit()

                flash(
                    "Цена семян удалена.",
                    "success",
                )

                return redirect(
                    url_for("main.admin_tariffs")
                )

        tariffs = get_tariffs(connection)

        seed_prices = connection.execute("""
            SELECT id, culture, price_per_seed
            FROM seed_prices
            ORDER BY culture COLLATE NOCASE
        """).fetchall()

    except sqlite3.Error:
        connection.rollback()

        flash(
            "Ошибка при работе с тарифами.",
            "error",
        )

        return redirect(url_for("main.profile"))

    finally:
        connection.close()

    return render_template(
        "admin_tariffs.html",
        tariffs=tariffs,
        seed_prices=seed_prices,
    )


# =========================================================
# УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ
# ТОЛЬКО ДЛЯ АДМИНИСТРАТОРА
# =========================================================

@main_bp.route(
    "/admin/users",
    methods=["GET", "POST"],
)
@role_required(ROLE_ADMIN)
def admin_users():
    if request.method == "POST":
        try:
            target_user_id = int(
                request.form.get("user_id", "")
            )

        except (TypeError, ValueError):
            flash(
                "Некорректный пользователь.",
                "error",
            )

            return redirect(
                url_for("main.admin_users")
            )

        new_role = request.form.get("role", "")

        # Через форму нельзя назначить другого администратора.
        if new_role not in (
            ROLE_USER,
            ROLE_EMPLOYEE,
        ):
            flash(
                "Можно назначить только клиента "
                "или сотрудника.",
                "error",
            )

            return redirect(
                url_for("main.admin_users")
            )

        if target_user_id == get_current_user_id():
            flash(
                "Нельзя изменить собственную роль "
                "через эту панель.",
                "error",
            )

            return redirect(
                url_for("main.admin_users")
            )

        connection = get_db_connection()

        try:
            target_user = connection.execute("""
                SELECT id
                FROM users
                WHERE id = ?
            """, (
                target_user_id,
            )).fetchone()

            if target_user is None:
                flash(
                    "Пользователь не найден.",
                    "error",
                )

                return redirect(
                    url_for("main.admin_users")
                )

            connection.execute("""
                UPDATE users
                SET role = ?
                WHERE id = ?
            """, (
                new_role,
                target_user_id,
            ))

            connection.commit()

            flash(
                "Роль пользователя обновлена.",
                "success",
            )

        except sqlite3.Error:
            connection.rollback()

            flash(
                "Не удалось изменить роль пользователя.",
                "error",
            )

        finally:
            connection.close()

        return redirect(
            url_for("main.admin_users")
        )

    connection = get_db_connection()

    try:
        users = connection.execute("""
            SELECT id, username, role
            FROM users
            ORDER BY id
        """).fetchall()

    finally:
        connection.close()

    return render_template(
        "admin_users.html",
        users=users,
        roles=ROLES,
    )


# =========================================================
# СТРАНИЦА 404
# =========================================================

@main_bp.app_errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404