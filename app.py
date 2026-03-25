import os

import requests
import streamlit as st


BASE_URL = "https://api.themoviedb.org/3"


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
        "extra_genre_count": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main() -> None:
    st.set_page_config(page_title="Movie Night Recommender", page_icon="🎬", layout="centered")
    ensure_state()

    st.title("Movie Night Recommender")
    st.write("Choose optional filters, get movie suggestions, and open one for more details.")

    if not TMDB_API_KEY:
        st.error("TMDB_API_KEY is missing. Add it to Streamlit secrets before deploying.")
        st.stop()

    genre_map, genre_options = load_genres()
    language_options = load_languages()
    language_codes = {label: code for label, code in language_options}
    addable_genres = [genre for genre in genre_options if genre != "Any"]

    genre_col, add_genre_col = st.columns([4, 1.3])
    with genre_col:
        genre = st.selectbox("Genre", genre_options, index=0)
    with add_genre_col:
        st.write("")
        st.write("")
        if st.button("+ Add Genre", use_container_width=True):
            st.session_state.extra_genre_count += 1
            st.rerun()

    extra_genres = []
    for index in range(st.session_state.extra_genre_count):
        extra_genre_col, remove_genre_col = st.columns([4, 1.3])
        with extra_genre_col:
            extra_genres.append(
                st.selectbox(
                    f"Extra Genre {index + 1}",
                    addable_genres,
                    key=f"extra_genre_{index}",
                )
            )
        with remove_genre_col:
            st.write("")
            st.write("")
            if st.button("Remove", key=f"remove_extra_genre_{index}", use_container_width=True):
                for shift_index in range(index, st.session_state.extra_genre_count - 1):
                    next_key = f"extra_genre_{shift_index + 1}"
                    current_key = f"extra_genre_{shift_index}"
                    if next_key in st.session_state:
                        st.session_state[current_key] = st.session_state[next_key]
                last_key = f"extra_genre_{st.session_state.extra_genre_count - 1}"
                if last_key in st.session_state:
                    del st.session_state[last_key]
                st.session_state.extra_genre_count -= 1
                st.rerun()

    actor = st.text_input("Actor", placeholder="Leave blank for any actor")
    year_mode = st.selectbox("Year Filter", ["Any", "Exactly", "Or Newer", "Or Older"], index=0)
    year = st.text_input(
        "Year",
        placeholder="Example: 2020",
        disabled=year_mode == "Any",
    )
    language_label_value = st.selectbox("Language", [label for label, _ in language_options], index=0)
    submitted = st.button("Get Recommendations", use_container_width=True)

    filters = {
        "genre": genre,
        "extra_genres": extra_genres,
        "actor": actor,
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
        st.markdown("### Recommendations")
        for index, movie in enumerate(recommendations, start=1):
            year_value = movie.get("release_date", "")[:4] if movie.get("release_date") else "N/A"
            label = f"{index}. {movie['title']} ({year_value})"
            if st.button(label, key=f"movie_{movie['id']}", use_container_width=True):
                st.session_state.selected_movie_id = movie["id"]
                st.rerun()

    if st.session_state.selected_movie_id is not None:
        movie_id = st.session_state.selected_movie_id
        try:
            payload = fetch_movie_details(movie_id)
            details = payload["details"]
            credits = payload["credits"]
            top_cast = [member["name"] for member in credits.get("cast", [])[:5]]
            genres = [genre["name"] for genre in details.get("genres", [])]

            st.markdown("### Movie Details")
            st.subheader(details["title"])
            st.write(f"Release Date: {details.get('release_date', 'N/A')}")
            st.write(f"Runtime: {details.get('runtime', 'N/A')} minutes")
            st.write(f"Genres: {', '.join(genres) if genres else 'N/A'}")
            st.write(f"Top Cast: {', '.join(top_cast) if top_cast else 'N/A'}")
            st.write("Description:")
            st.write(details.get("overview") or "No description available.")
        except Exception as error:
            st.error(str(error))

        if st.button("Back to Recommendations", use_container_width=True):
            st.session_state.selected_movie_id = None
            st.rerun()

    st.caption("Powered by TMDb. Narrow filters can produce only one result page.")


if __name__ == "__main__":
    main()
