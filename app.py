import math
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
def load_genres() -> tuple[dict[str, int], list[str]]:
    data = tmdb_get("/genre/movie/list", {"language": "en-US"})
    genres = data["genres"]
    genre_map = {genre["name"]: genre["id"] for genre in genres}
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
    actor_value: str,
    year_value: str,
    language_value: str,
    min_rating: float,
    genre_map: dict[str, int],
    page_number: int,
) -> dict:
    params = {
        "sort_by": "vote_average.desc",
        "vote_count.gte": 200,
        "include_adult": False,
        "page": page_number,
    }

    if genre_value != "Any":
        params["with_genres"] = genre_map[genre_value]

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
        params["primary_release_year"] = int(year_value)

    if language_value != "Any":
        params["with_original_language"] = language_value

    if min_rating > 0:
        params["vote_average.gte"] = min_rating

    return params


def fetch_recommendation_page(filters: dict, genre_map: dict[str, int], page_number: int) -> tuple[list[dict], int]:
    data = tmdb_get(
        "/discover/movie",
        build_discover_params(
            filters["genre"],
            filters["actor"],
            filters["year"],
            filters["language"],
            filters["min_rating"],
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


def load_page(filters: dict, genre_map: dict[str, int], page_number: int) -> None:
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

    with st.form("movie_filters"):
        genre = st.selectbox("Genre", genre_options, index=0)
        actor = st.text_input("Actor", placeholder="Leave blank for any actor")
        year = st.text_input("Release Year", placeholder="Example: 2020")
        min_rating = st.slider("Min Rating", min_value=0.0, max_value=10.0, value=6.0, step=0.5)
        language_label_value = st.selectbox("Language", [label for label, _ in language_options], index=0)
        submitted = st.form_submit_button("Get Recommendations", use_container_width=True)

    filters = {
        "genre": genre,
        "actor": actor,
        "year": year,
        "language": language_codes[language_label_value],
        "min_rating": min_rating,
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
        st.write(f"Genre: {current_filters['genre']}")
        st.write(f"Actor: {current_filters['actor'].strip() or 'Any'}")
        st.write(f"Release Year: {current_filters['year'].strip() or 'Any'}")
        st.write(f"Min Rating: {current_filters['min_rating']:.1f}")
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
            st.write(f"Rating: {details.get('vote_average', 0):.1f}/10")
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
