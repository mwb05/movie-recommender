import os

import requests
import streamlit as st


BASE_URL = "https://api.themoviedb.org/3"
POSTER_BASE_URL = "https://image.tmdb.org/t/p/w500"


def get_api_key() -> str | None:
    return st.secrets.get("TMDB_API_KEY") or os.getenv("TMDB_API_KEY")


TMDB_API_KEY = get_api_key()


def tmdb_get(endpoint: str, params: dict | None = None) -> dict:
    if not TMDB_API_KEY:
        raise RuntimeError("Missing TMDB_API_KEY. Add it to Streamlit secrets or environment variables.")

    query = dict(params or {})
    query["api_key"] = TMDB_API_KEY
    response = requests.get(f"{BASE_URL}{endpoint}", params=query, timeout=20)
    response.raise_for_status()
    return response.json()


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
    return results[0]["id"]


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
        if year_mode == "Exact year":
            params["primary_release_year"] = year_number
        elif year_mode == "Year and newer":
            params["primary_release_date.gte"] = f"{year_number}-01-01"
        elif year_mode == "Year and older":
            params["primary_release_date.lte"] = f"{year_number}-12-31"

    if language_value != "Any":
        params["with_original_language"] = language_value

    return params


def fetch_recommendation_page(filters: dict, genre_map: dict[str, str], page_number: int) -> tuple[list[dict], int]:
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
        ),
    )
    results = data.get("results", [])[:5]
    total_available_pages = min(data.get("total_pages", 1), 500)
    return results, total_available_pages


def language_label(language_options: list[tuple[str, str]], value: str) -> str:
    return next(label for label, code in language_options if code == value)


def reset_state_for_new_search() -> None:
    st.session_state.current_page = 0
    st.session_state.total_pages = 1
    st.session_state.recommendations = []
    st.session_state.selected_movie_id = None
    st.session_state.search_message = ""
    st.session_state.search_error = ""


def load_page(filters: dict, genre_map: dict[str, str], page_number: int) -> None:
    try:
        results, total_pages = fetch_recommendation_page(filters, genre_map, page_number)
        wrapped = False
        target_page = page_number

        if not results and page_number != 1:
            target_page = 1
            results, total_pages = fetch_recommendation_page(filters, genre_map, 1)
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
        "selected_movie_id": None,
        "search_message": "",
        "search_error": "",
        "filters": None,
        "extra_genres_list": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main() -> None:
    st.set_page_config(page_title="Movie Recommender", page_icon="🎬", layout="centered")
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

    genre_map, genre_options = load_genres()
    language_options = load_languages()
    language_codes = {label: code for label, code in language_options}
    addable_genres = [genre for genre in genre_options if genre != "Any"]
    certification_options = load_us_certifications()
    provider_map = load_watch_providers()
    provider_options = list(provider_map.keys())

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
        load_page(filters, genre_map, 1)

    current_filters = st.session_state.filters
    if current_filters and st.button("Get 5 Different", disabled=not st.session_state.recommendations, use_container_width=True):
        next_page = st.session_state.current_page + 1
        if next_page > max(st.session_state.total_pages, 1):
            next_page = 1
        load_page(current_filters, genre_map, next_page)

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
            st.write(f"Release Year: {current_filters['year'].strip()} {current_filters['year_mode']}")
        else:
            st.write("Release Year: Any")
        st.write(f"Language: {language_label(language_options, current_filters['language'])}")
        st.write(f"Page: {max(st.session_state.current_page, 1)} of {max(st.session_state.total_pages, 1)}")

    if st.session_state.search_error:
        st.error(st.session_state.search_error)
    elif st.session_state.search_message:
        st.info(st.session_state.search_message)

    recommendations = st.session_state.recommendations
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
        except Exception as error:
            st.error(str(error))

        if st.button("Back to Recommendations", use_container_width=True):
            st.session_state.selected_movie_id = None
            st.rerun()

    st.caption("Powered by TMDb. Streaming provider data is supplied via TMDb/JustWatch. Narrow filters can produce only one result page.")


if __name__ == "__main__":
    main()
