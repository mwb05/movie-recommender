import os
from collections import Counter

import psycopg
import requests
import streamlit as st
from psycopg.rows import dict_row


BASE_URL = "https://api.themoviedb.org/3"
POSTER_BASE_URL = "https://image.tmdb.org/t/p/w500"


def get_secret_value(name: str) -> str | None:
    try:
        return st.secrets.get(name) or os.getenv(name)
    except Exception:
        return os.getenv(name)


def get_api_key() -> str | None:
    return get_secret_value("TMDB_API_KEY")


TMDB_API_KEY = get_api_key()


def get_database_url() -> str | None:
    return get_secret_value("DATABASE_URL")


DATABASE_URL = get_database_url()


def tmdb_get(endpoint: str, params: dict | None = None) -> dict:
    if not TMDB_API_KEY:
        raise RuntimeError("Missing TMDB_API_KEY. Add it to Streamlit secrets or environment variables.")

    query = dict(params or {})
    query["api_key"] = TMDB_API_KEY
    response = requests.get(f"{BASE_URL}{endpoint}", params=query, timeout=20)
    response.raise_for_status()
    return response.json()


def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL. Add it to Streamlit secrets or environment variables.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db() -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_users (
                    id SERIAL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    pin TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS movies (
                    id SERIAL PRIMARY KEY,
                    tmdb_id INTEGER NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    release_date TEXT,
                    runtime INTEGER,
                    genres TEXT,
                    streaming_services TEXT,
                    language TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_movies (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
                    preference TEXT NOT NULL DEFAULT 'liked',
                    user_rating NUMERIC(2,1),
                    notes TEXT,
                    saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id, movie_id)
                )
                """
            )
            cur.execute(
                """
                ALTER TABLE user_movies
                ADD COLUMN IF NOT EXISTS preference TEXT NOT NULL DEFAULT 'liked'
                """
            )

            # Migrate older username-based users into the normalized app_users table only
            # when the legacy table has the columns we expect.
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'users'
                """
            )
            user_columns = {row["column_name"] for row in cur.fetchall()}
            if {"username", "pin"}.issubset(user_columns):
                cur.execute(
                    """
                    INSERT INTO app_users (username, pin)
                    SELECT username, pin
                    FROM users
                    ON CONFLICT (username) DO NOTHING
                    """
                )

            # Migrate denormalized saved_movies rows into movies + user_movies only when
            # the legacy table shape is available.
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'saved_movies'
                """
            )
            saved_movie_columns = {row["column_name"] for row in cur.fetchall()}
            required_saved_movie_columns = {
                "username",
                "tmdb_id",
                "title",
                "release_date",
                "runtime",
                "genres",
                "streaming_services",
                "language",
                "user_rating",
                "notes",
            }
            if required_saved_movie_columns.issubset(saved_movie_columns):
                cur.execute(
                    """
                    INSERT INTO movies (tmdb_id, title, release_date, runtime, genres, streaming_services, language)
                    SELECT DISTINCT tmdb_id, title, release_date, runtime, genres, streaming_services, language
                    FROM saved_movies
                    ON CONFLICT (tmdb_id) DO NOTHING
                    """
                )
                cur.execute(
                    """
                    INSERT INTO user_movies (user_id, movie_id, user_rating, notes)
                    SELECT DISTINCT au.id, m.id, sm.user_rating, sm.notes
                    FROM saved_movies sm
                    JOIN app_users au ON au.username = sm.username
                    JOIN movies m ON m.tmdb_id = sm.tmdb_id
                    ON CONFLICT (user_id, movie_id) DO NOTHING
                    """
                )
        conn.commit()


def username_exists(username: str) -> bool:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM app_users WHERE username = %s", (username,))
            row = cur.fetchone()
    return row is not None


def create_user(username: str, pin: str) -> bool:
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app_users (username, pin) VALUES (%s, %s)",
                    (username, pin),
                )
            conn.commit()
        return True
    except psycopg.IntegrityError:
        return False


def authenticate_user(username: str, pin: str) -> bool:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username FROM app_users WHERE username = %s AND pin = %s",
                (username, pin),
            )
            row = cur.fetchone()
    return row is not None


def fetch_saved_movies(username: str) -> list[dict]:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    um.id,
                    m.tmdb_id,
                    m.title,
                    m.genres,
                    m.release_date,
                    m.runtime,
                    m.streaming_services,
                    m.language,
                    um.preference,
                    um.user_rating,
                    um.notes
                FROM user_movies um
                JOIN app_users au ON au.id = um.user_id
                JOIN movies m ON m.id = um.movie_id
                WHERE au.username = %s
                ORDER BY LOWER(title), title
                """,
                (username,),
            )
            return cur.fetchall()


def fetch_saved_movie(username: str, tmdb_id: int) -> dict | None:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    um.id,
                    m.tmdb_id,
                    m.title,
                    m.genres,
                    m.release_date,
                    m.runtime,
                    m.streaming_services,
                    m.language,
                    um.preference,
                    um.user_rating,
                    um.notes
                FROM user_movies um
                JOIN app_users au ON au.id = um.user_id
                JOIN movies m ON m.id = um.movie_id
                WHERE au.username = %s AND m.tmdb_id = %s
                """,
                (username, tmdb_id),
            )
            return cur.fetchone()


def fetch_all_users() -> list[dict]:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, created_at
                FROM app_users
                ORDER BY id
                """
            )
            return cur.fetchall()


def fetch_all_movies() -> list[dict]:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tmdb_id, title, release_date, runtime, genres, language
                FROM movies
                ORDER BY id
                """
            )
            return cur.fetchall()


def fetch_user_movie_links() -> list[dict]:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    um.id,
                    au.username,
                    m.title,
                    um.user_rating,
                    um.notes,
                    um.saved_at
                FROM user_movies um
                JOIN app_users au ON au.id = um.user_id
                JOIN movies m ON m.id = um.movie_id
                ORDER BY um.id
                """
            )
            return cur.fetchall()


def save_movie_record(
    username: str,
    tmdb_id: int,
    title: str,
    genres: list[str],
    release_date: str,
    runtime: int | None,
    streaming_services: list[str],
    language: str,
    preference: str = "liked",
    user_rating: float | None = None,
    notes: str = "",
) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (
                    tmdb_id, title, release_date, runtime, genres, streaming_services, language
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tmdb_id) DO UPDATE
                SET
                    title = EXCLUDED.title,
                    release_date = EXCLUDED.release_date,
                    runtime = EXCLUDED.runtime,
                    genres = EXCLUDED.genres,
                    streaming_services = EXCLUDED.streaming_services,
                    language = EXCLUDED.language
                RETURNING id
                """,
                (
                    tmdb_id,
                    title,
                    release_date,
                    runtime,
                    ", ".join(genres),
                    ", ".join(streaming_services),
                    language,
                ),
            )
            movie_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO user_movies (user_id, movie_id, preference, user_rating, notes)
                SELECT id, %s, %s, %s, %s
                FROM app_users
                WHERE username = %s
                ON CONFLICT (user_id, movie_id) DO UPDATE
                SET preference = EXCLUDED.preference,
                    user_rating = EXCLUDED.user_rating,
                    notes = EXCLUDED.notes
                """,
                (movie_id, preference, user_rating, notes.strip() or None, username),
            )
        conn.commit()


def build_user_preference_profile(username: str, genre_map: dict[str, str]) -> dict:
    saved_movies = fetch_saved_movies(username)
    saved_tmdb_ids = {movie["tmdb_id"] for movie in saved_movies}
    disliked_movies = [movie for movie in saved_movies if movie.get("preference") == "disliked"]
    liked_movies = [movie for movie in saved_movies if movie.get("preference") != "disliked"]
    rated_movies = [movie for movie in liked_movies if movie.get("user_rating") is not None]
    favorite_movies = [movie for movie in rated_movies if movie["user_rating"] >= 4]
    source_movies = favorite_movies or rated_movies

    disliked_genre_counts: Counter[str] = Counter()
    disliked_language_counts: Counter[str] = Counter()
    for movie in disliked_movies:
        for genre_name in [item.strip() for item in (movie.get("genres") or "").split(",") if item.strip()]:
            disliked_genre_counts[genre_name] += 1
        language_value = (movie.get("language") or "").strip().lower()
        if language_value:
            disliked_language_counts[language_value] += 1

    if not source_movies and not disliked_movies:
        return {
            "favorite_count": 0,
            "top_genres": [],
            "preferred_genre_ids": set(),
            "saved_tmdb_ids": set(),
            "disliked_genre_ids": set(),
            "top_languages": [],
            "disliked_languages": [],
            "top_streaming_services": [],
        }

    genre_counts: Counter[str] = Counter()
    language_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()

    for movie in source_movies:
        for genre_name in [item.strip() for item in (movie.get("genres") or "").split(",") if item.strip()]:
            genre_counts[genre_name] += 1
        language_value = (movie.get("language") or "").strip().lower()
        if language_value:
            language_counts[language_value] += 1
        for provider_name in [item.strip() for item in (movie.get("streaming_services") or "").split(",") if item.strip()]:
            provider_counts[provider_name] += 1

    top_genres = [genre for genre, _ in genre_counts.most_common(3)]
    preferred_genre_ids = {
        int(genre_map[genre].split(",")[0])
        for genre in top_genres
        if genre in genre_map and genre_map[genre].split(",")[0].isdigit()
    }
    disliked_genre_ids = {
        int(genre_map[genre].split(",")[0])
        for genre, _ in disliked_genre_counts.most_common(3)
        if genre in genre_map and genre_map[genre].split(",")[0].isdigit()
    }

    return {
        "favorite_count": len(source_movies),
        "top_genres": top_genres,
        "preferred_genre_ids": preferred_genre_ids,
        "saved_tmdb_ids": saved_tmdb_ids,
        "disliked_genre_ids": disliked_genre_ids,
        "top_languages": [language for language, _ in language_counts.most_common(2)],
        "disliked_languages": [language for language, _ in disliked_language_counts.most_common(2)],
        "top_streaming_services": [provider for provider, _ in provider_counts.most_common(2)],
    }


def personalize_results(results: list[dict], filters: dict, profile: dict) -> list[dict]:
    if not results:
        return results

    filtered_results = [
        movie for movie in results if movie.get("id") not in profile.get("saved_tmdb_ids", set())
    ]
    if not filtered_results:
        return []

    def score(movie: dict) -> tuple[int, float]:
        points = 0
        genre_ids = set(movie.get("genre_ids", []))
        if profile["preferred_genre_ids"] and genre_ids & profile["preferred_genre_ids"]:
            points += 3
        if profile.get("disliked_genre_ids") and genre_ids & profile["disliked_genre_ids"]:
            points -= 4
        if profile["top_languages"]:
            movie_language = (movie.get("original_language") or "").strip().lower()
            if movie_language in profile["top_languages"]:
                points += 2
            if movie_language in profile.get("disliked_languages", []):
                points -= 3
        return (points, movie.get("vote_average", 0.0))

    return sorted(filtered_results, key=score, reverse=True)


def update_saved_movie(username: str, tmdb_id: int, preference: str, user_rating: float | None, notes: str) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_movies um
                SET preference = %s, user_rating = %s, notes = %s
                WHERE um.user_id = (
                    SELECT id FROM app_users WHERE username = %s
                )
                AND um.movie_id = (
                    SELECT id FROM movies WHERE tmdb_id = %s
                )
                """,
                (preference, user_rating, notes.strip() or None, username, tmdb_id),
            )
        conn.commit()


def delete_saved_movie(username: str, tmdb_id: int) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM user_movies um
                USING app_users au, movies m
                WHERE um.user_id = au.id
                  AND um.movie_id = m.id
                  AND au.username = %s
                  AND m.tmdb_id = %s
                """,
                (username, tmdb_id),
            )
        conn.commit()


@st.cache_data(show_spinner=False)
def load_genres() -> tuple[dict[str, str], list[str]]:
    data = tmdb_get("/genre/movie/list", {"language": "en-US"})
    genres = data["genres"]
    genre_map = {genre["name"]: str(genre["id"]) for genre in genres}
    if "Comedy" in genre_map and "Romance" in genre_map:
        # Treat romcom as movies tagged with both Comedy and Romance.
        genre_map["Romcom"] = f"{genre_map['Comedy']},{genre_map['Romance']}"
    genre_names = ["Any"] + sorted(genre_map.keys())
    return genre_map, genre_names


def load_languages() -> list[tuple[str, str]]:
    return [
        ("Any", "Any"),
        ("English", "en"),
        ("Spanish", "es"),
        ("French", "fr"),
        ("Japanese", "ja"),
        ("Korean", "ko"),
        ("Hindi", "hi"),
        ("Italian", "it"),
        ("German", "de"),
        ("Portuguese", "pt"),
    ]


def load_us_certifications() -> list[str]:
    return ["G", "PG", "PG-13", "R"]


@st.cache_data(show_spinner=False)
def load_watch_providers() -> dict[str, str]:
    data = tmdb_get("/watch/providers/movie", {"watch_region": "US", "language": "en-US"})
    results = data.get("results", [])
    provider_lookup = {provider["provider_name"]: str(provider["provider_id"]) for provider in results}
    alias_map = {
        "Netflix": ["Netflix"],
        "Disney+": ["Disney Plus", "Disney+"],
        "Hulu": ["Hulu"],
        "Max": ["Max", "HBO Max"],
        "Peacock": ["Peacock Premium", "Peacock"],
        "Paramount+": [
            "Paramount Plus",
            "Paramount+",
            "Paramount Plus Apple TV Channel",
            "Paramount Plus Premium",
            "Paramount+ Amazon Channel",
            "Paramount+ Roku Premium Channel",
            "Paramount+ with Showtime",
        ],
        "Prime Video": ["Amazon Prime Video", "Prime Video"],
        "Apple TV": ["Apple TV Plus", "Apple TV+", "Apple TV", "Apple TV Plus Amazon Channel"],
    }
    resolved_map: dict[str, str] = {}
    for app_label, candidates in alias_map.items():
        for candidate in candidates:
            if candidate in provider_lookup:
                resolved_map[app_label] = provider_lookup[candidate]
                break
    return resolved_map


@st.cache_data(show_spinner=False)
def fetch_movie_watch_providers(movie_id: int) -> dict:
    return tmdb_get(f"/movie/{movie_id}/watch/providers")


@st.cache_data(show_spinner=False)
def find_actor_id(actor_name: str) -> int | None:
    actor_name = actor_name.strip()
    if not actor_name:
        return None

    data = tmdb_get("/search/person", {"query": actor_name, "include_adult": False})
    results = data.get("results", [])
    if not results:
        return None

    lowered_name = actor_name.lower()
    for result in results:
        if result.get("name", "").strip().lower() == lowered_name:
            return result["id"]
    return results[0]["id"]


@st.cache_data(show_spinner=False)
def search_movie_by_title(title: str) -> list[dict]:
    title = title.strip()
    if not title:
        return []

    data = tmdb_get(
        "/search/movie",
        {
            "query": title,
            "include_adult": False,
            "language": "en-US",
        },
    )
    results = data.get("results", [])
    return results[:10]


def build_discover_params(
    genre_value: str,
    extra_genres: list[str],
    actor_value: str,
    certifications: list[str],
    providers: list[str],
    year_mode: str,
    year_value: str,
    language_value: str,
    genre_map: dict[str, str],
    page_number: int,
    profile: dict | None = None,
) -> dict:
    params = {
        "sort_by": "vote_average.desc",
        "vote_count.gte": 200,
        "include_adult": False,
        "page": page_number,
    }

    selected_genres = []
    if genre_value != "Any":
        selected_genres.append(genre_map[genre_value])
    for genre_name in extra_genres:
        if genre_name != "Any":
            selected_genres.append(genre_map[genre_name])
    if selected_genres:
        unique_ids = []
        for genre_id in selected_genres:
            if genre_id not in unique_ids:
                unique_ids.append(genre_id)
        params["with_genres"] = ",".join(unique_ids)
    elif profile and profile.get("preferred_genre_ids"):
        preferred_genres = sorted(profile["preferred_genre_ids"])
        params["with_genres"] = "|".join(str(genre_id) for genre_id in preferred_genres)
        params["sort_by"] = "popularity.desc"

    actor_value = actor_value.strip()
    if actor_value:
        actor_id = find_actor_id(actor_value)
        if actor_id is None:
            raise ValueError(f"No actor found for '{actor_value}'. Try a different spelling.")
        params["with_cast"] = actor_id

    params["certification_country"] = "US"
    params["certification.lte"] = "R"

    if certifications:
        params["certification"] = "|".join(certifications)

    if providers:
        params["watch_region"] = "US"
        params["with_watch_providers"] = "|".join(providers)

    year_value = year_value.strip()
    if year_value:
        if not year_value.isdigit() or len(year_value) != 4:
            raise ValueError("Release Year must be blank or a 4-digit year like 2020.")
        year_number = int(year_value)
        if year_mode == "Exactly":
            params["primary_release_year"] = year_number
        elif year_mode == "Or Newer":
            params["primary_release_date.gte"] = f"{year_number}-01-01"
        elif year_mode == "Or Older":
            params["primary_release_date.lte"] = f"{year_number}-12-31"

    if language_value != "Any":
        params["with_original_language"] = language_value
    elif profile and profile.get("top_languages"):
        params["with_original_language"] = profile["top_languages"][0]

    return params


def fetch_recommendation_page(
    filters: dict,
    genre_map: dict[str, str],
    page_number: int,
    profile: dict | None = None,
) -> tuple[list[dict], int]:
    data = tmdb_get(
        "/discover/movie",
        build_discover_params(
            filters["genre"],
            filters["extra_genres"],
            filters["actor"],
            filters["certifications"],
            filters["providers"],
            filters["year_mode"],
            filters["year"],
            filters["language"],
            genre_map,
            page_number,
            profile,
        ),
    )
    results = data.get("results", [])
    if profile:
        results = personalize_results(results, filters, profile)
    results = results[:5]
    total_available_pages = min(data.get("total_pages", 1), 500)
    return results, total_available_pages


def language_label(language_options: list[tuple[str, str]], value: str) -> str:
    return next(label for label, code in language_options if code == value)


def reset_state_for_new_search() -> None:
    st.session_state.current_page = 0
    st.session_state.total_pages = 1
    st.session_state.recommendations = []
    st.session_state.title_search_results = []
    st.session_state.selected_movie_id = None
    st.session_state.search_message = ""
    st.session_state.search_error = ""


def load_page(filters: dict, genre_map: dict[str, str], page_number: int, profile: dict | None = None) -> None:
    try:
        results, total_pages = fetch_recommendation_page(filters, genre_map, page_number, profile)
        wrapped = False
        target_page = page_number

        if not results and page_number != 1:
            target_page = 1
            results, total_pages = fetch_recommendation_page(filters, genre_map, 1, profile)
            wrapped = True

        st.session_state.total_pages = total_pages
        st.session_state.current_page = target_page
        st.session_state.recommendations = results
        st.session_state.selected_movie_id = None
        st.session_state.search_error = ""

        if not results:
            st.session_state.search_message = "No movies matched those filters. Try widening your search."
            return

        if wrapped:
            st.session_state.search_message = "Reached the end of the result pages, so the list started over from the beginning."
        elif total_pages == 1:
            st.session_state.search_message = "These filters only returned one result page, so 'Get 5 Different' will repeat this batch."
        else:
            st.session_state.search_message = ""

    except Exception as error:
        st.session_state.search_error = str(error)
        st.session_state.recommendations = []
        st.session_state.selected_movie_id = None


@st.cache_data(show_spinner=False)
def fetch_movie_details(movie_id: int) -> dict:
    details = tmdb_get(f"/movie/{movie_id}", {"language": "en-US"})
    credits = tmdb_get(f"/movie/{movie_id}/credits")
    return {"details": details, "credits": credits}


def ensure_state() -> None:
    defaults = {
        "current_page": 0,
        "total_pages": 1,
        "recommendations": [],
        "title_search_results": [],
        "selected_movie_id": None,
        "search_message": "",
        "search_error": "",
        "filters": None,
        "extra_genres_list": [],
        "username": "",
        "pin": "",
        "authenticated_user": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main() -> None:
    st.set_page_config(page_title="Movie Recommender", page_icon="🎬", layout="centered")
    init_db()
    ensure_state()

    st.markdown(
        """
        <style>
        .hero-card {
            padding: 1.6rem 1.4rem 1.2rem 1.4rem;
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 22px;
            background:
                radial-gradient(circle at top left, rgba(244, 114, 182, 0.18), transparent 28%),
                radial-gradient(circle at top right, rgba(96, 165, 250, 0.20), transparent 24%),
                linear-gradient(180deg, rgba(15, 23, 42, 0.96), rgba(17, 24, 39, 0.92));
            margin-bottom: 1rem;
        }
        .hero-kicker {
            color: #fda4af;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin-bottom: 0.5rem;
        }
        .hero-title {
            color: #f8fafc;
            font-size: 2.4rem;
            font-weight: 800;
            line-height: 1.0;
            margin: 0 0 0.7rem 0;
        }
        .hero-subtitle {
            color: #cbd5e1;
            font-size: 1rem;
            margin: 0;
        }
        .section-label {
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #94a3b8;
            margin-top: 0.4rem;
            margin-bottom: 0.35rem;
        }
        .filters-card {
            padding: 1.1rem 1rem 0.8rem 1rem;
            border-radius: 20px;
            border: 1px solid rgba(148, 163, 184, 0.14);
            background: rgba(15, 23, 42, 0.45);
            margin-bottom: 1rem;
        }
        .results-card {
            padding: 1rem 1rem 0.6rem 1rem;
            border-radius: 20px;
            border: 1px solid rgba(148, 163, 184, 0.14);
            background: rgba(15, 23, 42, 0.35);
            margin-top: 1rem;
        }
        @media (max-width: 640px) {
            .hero-title {
                font-size: 1.85rem;
            }
        }
        </style>
        <div class="hero-card">
            <div class="hero-kicker">Discover Something Great</div>
            <div class="hero-title">Movie Recommender</div>
            <p class="hero-subtitle">Choose a few filters, explore recommendation batches, and open any movie for a quick breakdown.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not TMDB_API_KEY:
        st.error("TMDB_API_KEY is missing. Add it to Streamlit secrets before deploying.")
        st.stop()
    if not DATABASE_URL:
        st.error("DATABASE_URL is missing. Add your Postgres connection string to Streamlit secrets before deploying.")
        st.stop()

    genre_map, genre_options = load_genres()
    language_options = load_languages()
    language_codes = {label: code for label, code in language_options}
    addable_genres = [genre for genre in genre_options if genre != "Any"]
    certification_options = load_us_certifications()
    provider_map = load_watch_providers()
    provider_options = list(provider_map.keys())
    st.markdown("### User Login")
    auth_name_col, auth_pin_col = st.columns(2)
    with auth_name_col:
        entered_username = st.text_input(
            "Username",
            value=st.session_state.username,
            placeholder="Choose or enter a username",
        ).strip()
    with auth_pin_col:
        entered_pin = st.text_input(
            "PIN",
            value=st.session_state.pin,
            placeholder="4 digits or any short PIN",
            type="password",
        ).strip()
    st.session_state.username = entered_username
    st.session_state.pin = entered_pin

    login_col, create_col, logout_col = st.columns(3)
    with login_col:
        login_clicked = st.button("Log In", use_container_width=True)
    with create_col:
        create_clicked = st.button("Create User", use_container_width=True)
    with logout_col:
        logout_clicked = st.button("Log Out", use_container_width=True, disabled=not st.session_state.authenticated_user)

    if create_clicked:
        if not entered_username or not entered_pin:
            st.error("Enter both a username and PIN to create an account.")
        elif username_exists(entered_username):
            st.error("That username is already taken. Try logging in or choose another one.")
        elif create_user(entered_username, entered_pin):
            st.session_state.authenticated_user = entered_username
            st.success(f"Account created. You are logged in as {entered_username}.")
        else:
            st.error("Could not create that username.")

    if login_clicked:
        if not entered_username or not entered_pin:
            st.error("Enter both a username and PIN to log in.")
        elif authenticate_user(entered_username, entered_pin):
            st.session_state.authenticated_user = entered_username
            st.success(f"Logged in as {entered_username}.")
        else:
            st.error("Username or PIN did not match.")

    if logout_clicked:
        st.session_state.authenticated_user = ""
        st.session_state.selected_movie_id = None
        st.session_state.title_search_results = []
        st.session_state.recommendations = []
        st.session_state.search_message = ""
        st.success("You have been logged out.")

    active_user = st.session_state.authenticated_user
    if active_user:
        st.caption(f"Signed in as: {active_user}")
    else:
        st.info("Create a username and PIN, or log in to save movies and keep your preferences private.")

    user_profile = build_user_preference_profile(active_user, genre_map) if active_user else None

    if active_user:
        st.markdown("### Your Taste Profile")
        if user_profile and user_profile["favorite_count"]:
            liked_genres = ", ".join(user_profile["top_genres"]) if user_profile["top_genres"] else "Still learning"
            liked_languages = ", ".join(language.upper() for language in user_profile["top_languages"]) if user_profile["top_languages"] else "Still learning"
            liked_services = ", ".join(user_profile["top_streaming_services"]) if user_profile["top_streaming_services"] else "Still learning"
            st.write(f"Based on {user_profile['favorite_count']} rated saved movie(s), you seem to like: **{liked_genres}**.")
            st.write(f"Preferred languages: {liked_languages}")
            st.write(f"Common streaming picks: {liked_services}")
            st.caption("With no genre or language filters, the app now uses your saved taste to shape the recommendation query itself, then sorts the results using your ratings and preferences.")
        else:
            st.info("Rate a few saved movies and the app will start tailoring recommendation order to your taste.")

    st.markdown("### Search by Title")
    title_search_col, title_button_col = st.columns([4, 1.3], vertical_alignment="bottom")
    with title_search_col:
        title_search = st.text_input("Movie Title", placeholder="Search for a movie by title")
    with title_button_col:
        title_submitted = st.button("Find Movie", use_container_width=True)

    if title_submitted:
        try:
            matches = search_movie_by_title(title_search)
            st.session_state.recommendations = []
            if not matches:
                st.session_state.title_search_results = []
                st.session_state.search_error = ""
                st.session_state.search_message = f"No movie found for '{title_search.strip()}'."
                st.session_state.selected_movie_id = None
            else:
                st.session_state.title_search_results = matches
                st.session_state.selected_movie_id = None
                st.session_state.search_error = ""
                st.session_state.search_message = f"Found {len(matches)} title matches."
        except Exception as error:
            st.session_state.search_error = str(error)
            st.session_state.title_search_results = []
            st.session_state.selected_movie_id = None

    st.markdown('<div class="filters-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-label">Filters</div>', unsafe_allow_html=True)

    genre_col, add_genre_col = st.columns([4, 1.3], vertical_alignment="bottom")
    with genre_col:
        genre = st.selectbox("Genre", genre_options, index=0)
    with add_genre_col:
        if st.button("+ Add Genre", use_container_width=True):
            st.session_state.extra_genres_list.append(addable_genres[0])
            st.rerun()

    extra_genres = []
    for index, current_genre in enumerate(st.session_state.extra_genres_list):
        extra_genre_col, remove_genre_col = st.columns([4, 1.3], vertical_alignment="bottom")
        with extra_genre_col:
            extra_genres.append(
                st.selectbox(
                    f"Extra Genre {index + 1}",
                    addable_genres,
                    index=addable_genres.index(current_genre) if current_genre in addable_genres else 0,
                    key=f"extra_genre_{index}",
                )
            )
        with remove_genre_col:
            if st.button("Remove", key=f"remove_extra_genre_{index}", use_container_width=True):
                st.session_state.extra_genres_list.pop(index)
                stale_key = f"extra_genre_{len(st.session_state.extra_genres_list)}"
                if stale_key in st.session_state:
                    del st.session_state[stale_key]
                st.rerun()

    st.session_state.extra_genres_list = list(extra_genres)

    actor = st.text_input("Actor", placeholder="Leave blank for any actor")
    st.markdown('<div class="section-label">Allowed Ratings</div>', unsafe_allow_html=True)
    cert_columns = st.columns(len(certification_options))
    selected_certifications = []
    for cert, column in zip(certification_options, cert_columns):
        with column:
            if st.checkbox(cert, key=f"cert_{cert}"):
                selected_certifications.append(cert)
    st.markdown('<div class="section-label">Streaming Services (US)</div>', unsafe_allow_html=True)
    provider_columns = st.columns(2)
    selected_providers = []
    for index, provider_name in enumerate(provider_options):
        with provider_columns[index % 2]:
            if st.checkbox(provider_name, key=f"provider_{provider_name}"):
                selected_providers.append(provider_map[provider_name])
    year_filter_col, year_value_col = st.columns(2)
    with year_filter_col:
        year_mode = st.selectbox("Year Filter", ["Any", "Exactly", "Or Newer", "Or Older"], index=0)
    with year_value_col:
        year = st.text_input(
            "Year",
            placeholder="Example: 2020",
            disabled=year_mode == "Any",
        )

    language_label_value = st.selectbox("Language", [label for label, _ in language_options], index=0)
    submitted = st.button("Get Recommendations", type="primary", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    filters = {
        "genre": genre,
        "extra_genres": extra_genres,
        "actor": actor,
        "certifications": selected_certifications,
        "providers": selected_providers,
        "year_mode": year_mode,
        "year": year,
        "language": language_codes[language_label_value],
    }

    if submitted:
        reset_state_for_new_search()
        st.session_state.filters = filters
        load_page(filters, genre_map, 1, user_profile)

    current_filters = st.session_state.filters
    if current_filters and st.button("Get 5 Different", disabled=not st.session_state.recommendations, use_container_width=True):
        next_page = st.session_state.current_page + 1
        if next_page > max(st.session_state.total_pages, 1):
            next_page = 1
        load_page(current_filters, genre_map, next_page, user_profile)

    if st.session_state.search_error:
        st.error(st.session_state.search_error)
    elif st.session_state.search_message:
        st.info(st.session_state.search_message)

    recommendations = st.session_state.recommendations
    title_search_results = st.session_state.title_search_results
    if title_search_results and st.session_state.selected_movie_id is None:
        st.markdown('<div class="results-card">', unsafe_allow_html=True)
        st.markdown("### Title Matches")
        for index, movie in enumerate(title_search_results, start=1):
            year_value = movie.get("release_date", "")[:4] if movie.get("release_date") else "N/A"
            overview = movie.get("overview") or "No description available."
            st.markdown(f"**{index}. {movie['title']} ({year_value})**")
            st.caption(overview[:180] + ("..." if len(overview) > 180 else ""))
            if st.button("Open Details", key=f"title_match_{movie['id']}", use_container_width=True):
                st.session_state.selected_movie_id = movie["id"]
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    if recommendations and st.session_state.selected_movie_id is None:
        st.markdown('<div class="results-card">', unsafe_allow_html=True)
        st.markdown("### Recommendations")
        for index, movie in enumerate(recommendations, start=1):
            year_value = movie.get("release_date", "")[:4] if movie.get("release_date") else "N/A"
            label = f"{index}. {movie['title']} ({year_value})"
            if st.button(label, key=f"movie_{movie['id']}", use_container_width=True):
                st.session_state.selected_movie_id = movie["id"]
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.selected_movie_id is not None:
        movie_id = st.session_state.selected_movie_id
        try:
            payload = fetch_movie_details(movie_id)
            details = payload["details"]
            credits = payload["credits"]
            provider_payload = fetch_movie_watch_providers(movie_id)
            top_cast = [member["name"] for member in credits.get("cast", [])[:5]]
            genres = [genre["name"] for genre in details.get("genres", [])]
            us_provider_data = provider_payload.get("results", {}).get("US", {})
            flatrate_providers = us_provider_data.get("flatrate", [])
            buy_providers = us_provider_data.get("buy", [])
            rent_providers = us_provider_data.get("rent", [])
            provider_names = [provider["provider_name"] for provider in flatrate_providers]
            buy_provider_names = [provider["provider_name"] for provider in buy_providers]
            rent_provider_names = [provider["provider_name"] for provider in rent_providers]
            active_username = st.session_state.authenticated_user
            saved_movie = fetch_saved_movie(active_username, movie_id) if active_username else None

            st.markdown("### Movie Details")
            poster_path = details.get("poster_path")
            details_col, poster_col = st.columns([2.2, 1], vertical_alignment="top")
            with details_col:
                st.subheader(details["title"])
                st.write(f"Release Date: {details.get('release_date', 'N/A')}")
                st.write(f"Runtime: {details.get('runtime', 'N/A')} minutes")
                st.write(f"Genres: {', '.join(genres) if genres else 'N/A'}")
                st.write(f"Top Cast: {', '.join(top_cast) if top_cast else 'N/A'}")
                st.write(f"Streaming On (US): {', '.join(provider_names) if provider_names else 'Not listed for streaming'}")
                if not provider_names and (buy_provider_names or rent_provider_names):
                    st.write(f"Buy/Rent On (US): {', '.join(buy_provider_names or rent_provider_names)}")
                st.write("Description:")
                st.write(details.get("overview") or "No description available.")
            with poster_col:
                if poster_path:
                    st.image(f"{POSTER_BASE_URL}{poster_path}", use_container_width=True)

            if not active_username:
                st.info("Enter a username above to save and manage movies in your personal database.")
            elif saved_movie is None:
                initial_preference = st.selectbox(
                    "How do you feel about this movie?",
                    ["Liked", "Disliked"],
                    index=0,
                    key=f"new_preference_{movie_id}",
                )
                initial_rating = st.number_input(
                    "Your Rating",
                    min_value=0.0,
                    max_value=5.0,
                    value=0.0,
                    step=0.5,
                    key=f"new_rating_{movie_id}",
                )
                initial_notes = st.text_area(
                    "Notes",
                    value="",
                    placeholder="What did you like about this movie?",
                    key=f"new_notes_{movie_id}",
                )
                if st.button("Save to Database", use_container_width=True):
                    normalized_rating = initial_rating if initial_rating > 0 else None
                    save_movie_record(
                        username=active_username,
                        tmdb_id=movie_id,
                        title=details["title"],
                        genres=genres,
                        release_date=details.get("release_date") or "",
                        runtime=details.get("runtime"),
                        streaming_services=provider_names,
                        language=details.get("original_language", "").upper() or "N/A",
                        preference=initial_preference.lower(),
                        user_rating=normalized_rating,
                        notes=initial_notes,
                    )
                    st.success(f"{details['title']} was added to your saved movies database.")
                    st.rerun()
            else:
                st.success("This movie is already saved in your database.")

                preference_options = ["Liked", "Disliked"]
                saved_preference = "Disliked" if saved_movie.get("preference") == "disliked" else "Liked"
                preference_value = st.selectbox(
                    "How do you feel about this movie?",
                    preference_options,
                    index=preference_options.index(saved_preference),
                    key=f"preference_{movie_id}",
                )
                default_rating = float(saved_movie["user_rating"]) if saved_movie["user_rating"] is not None else 0.0
                rating_value = st.number_input(
                    "Your Rating",
                    min_value=0.0,
                    max_value=5.0,
                    value=default_rating,
                    step=0.5,
                    key=f"rating_{movie_id}",
                )
                notes_value = st.text_area(
                    "Notes",
                    value=saved_movie["notes"] or "",
                    placeholder="Why did you save this one?",
                    key=f"notes_{movie_id}",
                )
                update_col, delete_col = st.columns(2)
                with update_col:
                    if st.button("Update Saved Movie", use_container_width=True):
                        normalized_rating = rating_value if rating_value > 0 else None
                        update_saved_movie(
                            active_username,
                            movie_id,
                            preference_value.lower(),
                            normalized_rating,
                            notes_value,
                        )
                        st.success("Saved movie updated. Future recommendations will use your latest rating.")
                        st.rerun()
                with delete_col:
                    if st.button("Delete Saved Movie", use_container_width=True):
                        delete_saved_movie(active_username, movie_id)
                        st.success("Saved movie deleted.")
                        st.rerun()
        except Exception as error:
            st.error(str(error))

        if st.button("Back to Recommendations", use_container_width=True):
            st.session_state.selected_movie_id = None
            st.rerun()

    st.markdown("### Saved Movies Database")
    if active_user:
        saved_movies = fetch_saved_movies(active_user)
        if saved_movies:
            for movie in saved_movies:
                st.markdown(
                    f"""
                    **{movie['title']}**  
                    Release Date: {movie['release_date'] or 'N/A'}  
                    Genres: {movie['genres'] or 'N/A'}  
                    Streaming: {movie['streaming_services'] or 'Not listed'}  
                    Preference: {movie['preference'].title() if movie.get('preference') else 'Liked'}  
                    Your Rating: {movie['user_rating'] if movie['user_rating'] is not None else 'Not rated'}  
                    Notes: {movie['notes'] or 'No notes yet'}
                    """
                )
        else:
            st.info("No saved movies yet for this username. Open a recommendation and click 'Save to Database' to create your first record.")
    else:
        st.info("Log in to view and manage your saved movies.")

    if current_filters:
        st.markdown("### Selected Filters")
        genre_parts = [current_filters["genre"]] if current_filters["genre"] != "Any" else []
        genre_parts.extend(current_filters["extra_genres"])
        st.write(f"Genre: {', '.join(genre_parts) if genre_parts else 'Any'}")
        st.write(f"Actor: {current_filters['actor'].strip() or 'Any'}")
        st.write(f"Allowed Ratings: {', '.join(current_filters['certifications']) if current_filters['certifications'] else 'Any'}")
        selected_provider_labels = [
            provider_name for provider_name, provider_id in provider_map.items()
            if provider_id in current_filters["providers"]
        ]
        st.write(f"Streaming Services: {', '.join(selected_provider_labels) if selected_provider_labels else 'Any'}")
        if current_filters["year"].strip():
            st.write(f"Release Year: {current_filters['year_mode']} {current_filters['year'].strip()}")
        else:
            st.write("Release Year: Any")
        st.write(f"Language: {language_label(language_options, current_filters['language'])}")
        st.write(f"Page: {max(st.session_state.current_page, 1)} of {max(st.session_state.total_pages, 1)}")

    st.caption(
        "Powered by TMDb. Streaming provider data is supplied via TMDb/JustWatch. "
        "Saved movies are stored locally in SQLite for CRUD operations."
    )


if __name__ == "__main__":
    main()
