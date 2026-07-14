"""
Spotify MCP Server
==================
MCP server for Spotify Web API.
Uses httpx async directly — no spotipy dependency.

Tools:
  - spotify_playback    : Playback control (get/start/pause/skip)
  - spotify_search      : Search (track/album/artist/playlist)
  - spotify_queue       : Queue management (get/add)
  - spotify_get_info    : Get item details by URI
  - spotify_playlist    : Playlist CRUD
  - spotify_liked_songs : Liked songs retrieval

Usage:
  spotify-mcp          # stdio mode (for MCP clients)
  spotify-mcp --auth   # Initial OAuth authentication
"""

import asyncio
import json
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from pydantic import Field
from mcp.server.fastmcp import FastMCP

# ── Server ────────────────────────────────────────────────────────────────────
mcp = FastMCP("spotify-mcp")

# ── Constants ─────────────────────────────────────────────────────────────────
SPOTIFY_API = "https://api.spotify.com/v1"
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback")

CACHE_PATH = os.environ.get(
    "SPOTIFY_CACHE_PATH",
    os.path.join(os.path.expanduser("~"), ".spotify_mcp_cache.json"),
)

SCOPES = " ".join([
    "user-library-read",
    "user-library-modify",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-read-recently-played",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
])

DEV_LIMIT = 10  # Spotify Dev Mode: max search results per request (Feb 2026)
PAGE_LIMIT = 50  # Spotify max page size for playlist/library item listings

TRACKS_UNAVAILABLE = (
    "Track list unavailable: this app cannot read the contents of playlists it "
    "does not own (Spotify returns 403)."
)


# ── Token Management ─────────────────────────────────────────────────────────
_token_cache: dict = {}


def _load_token() -> dict:
    global _token_cache
    if _token_cache:
        return _token_cache
    try:
        with open(CACHE_PATH) as f:
            _token_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return _token_cache


def _save_token(token: dict):
    global _token_cache
    _token_cache = token
    with open(CACHE_PATH, "w") as f:
        json.dump(token, f, indent=2)


async def _get_access_token() -> str:
    token = _load_token()
    if not token:
        raise RuntimeError(
            "No Spotify token found. Run `spotify-mcp --auth` first."
        )
    if token.get("expires_at", 0) < time.time() + 60:
        token = await _refresh_token(token["refresh_token"])
    return token["access_token"]


async def _refresh_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as c:
        resp = await c.post(SPOTIFY_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        resp.raise_for_status()
        data = resp.json()
        data["refresh_token"] = data.get("refresh_token", refresh_token)
        data["expires_at"] = time.time() + data.get("expires_in", 3600)
        _save_token(data)
        return data


# ── HTTP Helpers ─────────────────────────────────────────────────────────────
_http: Optional[httpx.AsyncClient] = None


def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=15)
    return _http


async def _api(method: str, path: str, **kwargs) -> Optional[dict]:
    """Make an authenticated request to the Spotify API."""
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = await _get_http().request(
        method, f"{SPOTIFY_API}/{path}", headers=headers, **kwargs
    )
    resp.raise_for_status()
    if resp.status_code == 204 or not resp.content:
        return None
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return None


async def _get(path: str, **params) -> Optional[dict]:
    return await _api("GET", path, params=params)


async def _put(path: str, json_data: dict = None) -> Optional[dict]:
    return await _api("PUT", path, json=json_data)


async def _post(path: str, json_data: dict = None) -> Optional[dict]:
    return await _api("POST", path, json=json_data)


async def _delete(path: str, json_data: dict = None) -> Optional[dict]:
    return await _api("DELETE", path, json=json_data)


def _handle_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        try:
            msg = e.response.json().get("error", {}).get("message", "")
        except Exception:
            msg = ""
        if status == 401:
            return f"Error 401: Token expired or invalid. Try `spotify-mcp --auth`. {msg}"
        if status == 403:
            return f"Error 403: Forbidden. {msg}"
        if status == 404:
            return f"Error 404: Not found. {msg}"
        if status == 429:
            return "Error 429: Rate limited. Try again later."
        return f"Error {status}: {msg or 'Spotify API error.'}"
    return f"Error: {e}"


# ── Parsers ──────────────────────────────────────────────────────────────────
def _parse_track(t: dict, detailed: bool = False) -> Optional[dict]:
    if not t:
        return None
    r = {"name": t["name"], "id": t["id"]}
    if "is_playing" in t:
        r["is_playing"] = t["is_playing"]
    if not t.get("is_playable", True):
        r["is_playable"] = False
    artists = t.get("artists", [])
    if detailed:
        r["artists"] = [{"name": a["name"], "id": a["id"]} for a in artists]
        if album := t.get("album"):
            r["album"] = _parse_album(album)
        for k in ("track_number", "duration_ms"):
            if k in t:
                r[k] = t[k]
    else:
        names = [a["name"] for a in artists]
        if len(names) == 1:
            r["artist"] = names[0]
        elif names:
            r["artists"] = names
    return r


def _parse_album(a: dict, detailed: bool = False) -> dict:
    if not a:
        return {}
    r = {"name": a["name"], "id": a["id"]}
    artists = a.get("artists", [])
    if detailed:
        r["artists"] = [{"name": x["name"], "id": x["id"]} for x in artists]
        for k in ("total_tracks", "release_date", "genres"):
            if k in a:
                r[k] = a[k]
        if tracks := a.get("tracks", {}).get("items"):
            r["tracks"] = [_parse_track(t) for t in tracks if t]
    else:
        names = [x["name"] for x in artists]
        if len(names) == 1:
            r["artist"] = names[0]
        elif names:
            r["artists"] = names
    return r


def _parse_artist(a: dict, detailed: bool = False) -> Optional[dict]:
    if not a:
        return None
    r = {"name": a["name"], "id": a["id"]}
    if detailed and "genres" in a:
        r["genres"] = a["genres"]
    return r


def _parse_playlist(p: dict, username: str = None, detailed: bool = False) -> Optional[dict]:
    """Parse playlist. Handles Feb 2026 API field renames: tracks->items, track->item."""
    if not p:
        return None
    # Spotify omits the track container entirely for playlists this app may not
    # read. A missing container is not an empty one: defaulting it to {} here
    # would report a populated playlist as having 0 tracks.
    content = p.get("items", p.get("tracks"))
    if not isinstance(content, dict):
        content = None
    r = {
        "name": p.get("name"),
        "id": p.get("id"),
        "owner": p.get("owner", {}).get("display_name"),
        "total_tracks": content.get("total", 0) if content is not None else None,
    }
    if username:
        r["user_is_owner"] = r["owner"] == username
    if content is None:
        r["error"] = TRACKS_UNAVAILABLE
    if detailed:
        r["description"] = p.get("description")
        if content is not None:
            r["tracks"] = [
                _parse_track(item.get("item") or item.get("track"))
                for item in content.get("items", [])
                if item and (item.get("item") or item.get("track"))
            ]
    return r


# ── Username Cache ───────────────────────────────────────────────────────────
_username: Optional[str] = None


async def _get_username() -> str:
    global _username
    if _username is None:
        me = await _get("me")
        _username = me["display_name"]
    return _username


# ── Device Helper ────────────────────────────────────────────────────────────
async def _ensure_device() -> Optional[str]:
    """Return active device ID, or first available one."""
    data = await _get("me/player/devices")
    devices = data.get("devices", []) if data else []
    if not devices:
        return None
    for d in devices:
        if d.get("is_active"):
            return d["id"]
    return devices[0]["id"]


# ── Tools ────────────────────────────────────────────────────────────────────
@mcp.tool()
async def spotify_playback(
    action: str = Field(description="Action: 'get', 'start', 'pause', 'skip', 'previous', or 'volume'"),
    spotify_uri: Optional[str] = Field(
        default=None,
        description="Spotify URI to play (e.g. spotify:track:xxx). For 'start' action.",
    ),
    num_skips: int = Field(default=1, description="Number of tracks to skip (for 'skip' action)"),
    volume_percent: Optional[int] = Field(default=None, description="Volume level 0-100 (for 'volume' action)"),
) -> str:
    """Control Spotify playback - get current track, start/pause/skip, go to previous track, or set volume."""
    try:
        match action:
            case "get":
                data = await _get("me/player/currently-playing")
                if not data or data.get("currently_playing_type") != "track":
                    return "No track playing."
                track = _parse_track(data["item"])
                track["is_playing"] = data.get("is_playing", False)
                return json.dumps(track, indent=2)

            case "start":
                body = {}
                if spotify_uri:
                    if spotify_uri.startswith("spotify:track:"):
                        body["uris"] = [spotify_uri]
                    else:
                        body["context_uri"] = spotify_uri
                device_id = await _ensure_device()
                if not device_id and not spotify_uri:
                    return "No active device found. Open Spotify first."
                path = "me/player/play"
                if device_id:
                    path += f"?device_id={device_id}"
                await _put(path, body if body else None)
                return "Playback started."

            case "pause":
                await _put("me/player/pause")
                return "Playback paused."

            case "skip":
                for _ in range(num_skips):
                    await _post("me/player/next")
                return f"Skipped {num_skips} track(s)."

            case "previous":
                await _post("me/player/previous")
                return "Skipped to previous track."

            case "volume":
                if volume_percent is None:
                    return "Error: volume_percent is required (0-100)."
                vol = max(0, min(100, volume_percent))
                await _put(f"me/player/volume?volume_percent={vol}")
                return f"Volume set to {vol}%."

            case _:
                return f"Unknown action: {action}"
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def spotify_search(
    query: str = Field(description="Search query"),
    qtype: str = Field(
        default="track",
        description="Type: 'track', 'album', 'artist', 'playlist' (comma-separated for multiple)",
    ),
    limit: int = Field(default=5, description="Max results per type (max 10)"),
) -> str:
    """Search Spotify for tracks, albums, artists, or playlists."""
    try:
        limit = min(limit, DEV_LIMIT)
        data = await _get("search", q=query, type=qtype, limit=limit)
        if not data:
            return "No results found."
        username = await _get_username()
        results = {}
        for q in qtype.split(","):
            q = q.strip()
            key = q + "s"
            items = data.get(key, {}).get("items", [])
            match q:
                case "track":
                    results[key] = [_parse_track(i) for i in items if i]
                case "album":
                    results[key] = [_parse_album(i) for i in items if i]
                case "artist":
                    results[key] = [_parse_artist(i) for i in items if i]
                case "playlist":
                    results[key] = [_parse_playlist(i, username) for i in items if i]
        return json.dumps(results, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def spotify_queue(
    action: str = Field(description="Action: 'get' or 'add'"),
    track_uri: Optional[str] = Field(
        default=None, description="Spotify track URI to add (for 'add' action)"
    ),
) -> str:
    """View the playback queue or add a track to it."""
    try:
        match action:
            case "get":
                data = await _get("me/player/queue")
                if not data:
                    return "Queue is empty."
                result = {
                    "currently_playing": _parse_track(data.get("currently_playing")),
                    "queue": [_parse_track(t) for t in data.get("queue", []) if t],
                }
                return json.dumps(result, indent=2)

            case "add":
                if not track_uri:
                    return "Error: track_uri is required."
                await _post(f"me/player/queue?uri={track_uri}")
                return "Track added to queue."

            case _:
                return f"Unknown action: {action}"
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def spotify_get_info(
    item_uri: str = Field(description="Spotify URI (e.g. spotify:track:xxx, spotify:album:xxx)"),
) -> str:
    """Get detailed information about any Spotify item by URI."""
    try:
        parts = item_uri.split(":")
        if len(parts) != 3:
            return f"Invalid URI format: {item_uri}"
        _, qtype, item_id = parts

        match qtype:
            case "track":
                data = await _get(f"tracks/{item_id}")
                return json.dumps(_parse_track(data, detailed=True), indent=2)

            case "album":
                data = await _get(f"albums/{item_id}")
                return json.dumps(_parse_album(data, detailed=True), indent=2)

            case "artist":
                data = await _get(f"artists/{item_id}")
                result = _parse_artist(data, detailed=True)
                albums = await _get(f"artists/{item_id}/albums", limit=DEV_LIMIT)
                if albums and albums.get("items"):
                    result["albums"] = [_parse_album(a) for a in albums["items"]]
                return json.dumps(result, indent=2)

            case "playlist":
                username = await _get_username()
                data = await _get(f"playlists/{item_id}")
                return json.dumps(_parse_playlist(data, username, detailed=True), indent=2)

            case _:
                return f"Unknown type: {qtype}"
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def spotify_playlist(
    action: str = Field(
        description="Action: 'get', 'get_tracks', 'add_tracks', 'remove_tracks', "
                    "'change_details', 'create', 'delete'"
    ),
    playlist_id: Optional[str] = Field(default=None, description="Playlist ID"),
    track_ids: Optional[list[str]] = Field(
        default=None, description="List of track IDs or URIs"
    ),
    name: Optional[str] = Field(default=None, description="Playlist name (for create/change_details)"),
    description: Optional[str] = Field(default=None, description="Playlist description"),
    public: bool = Field(default=True, description="Whether playlist is public"),
) -> str:
    """Manage Spotify playlists - list, create, modify, delete."""
    try:
        username = await _get_username()

        match action:
            case "get":
                data = await _get("me/playlists")
                if not data or not data.get("items"):
                    return "No playlists found."
                playlists = [_parse_playlist(p, username) for p in data["items"] if p]
                return json.dumps(playlists, indent=2)

            case "get_tracks":
                if not playlist_id:
                    return "Error: playlist_id is required."
                tracks = []
                offset = 0
                while True:
                    data = await _get(
                        f"playlists/{playlist_id}/items",
                        limit=PAGE_LIMIT,
                        offset=offset,
                    )
                    if not data or not data.get("items"):
                        break
                    tracks.extend(
                        _parse_track(item.get("item") or item.get("track"))
                        for item in data["items"]
                        if item and (item.get("item") or item.get("track"))
                    )
                    if not data.get("next"):
                        break
                    offset += PAGE_LIMIT
                return json.dumps(tracks, indent=2)

            case "add_tracks":
                if not playlist_id or not track_ids:
                    return "Error: playlist_id and track_ids are required."
                uris = [
                    f"spotify:track:{t}" if not t.startswith("spotify:") else t
                    for t in track_ids
                ]
                await _post(f"playlists/{playlist_id}/items", {"uris": uris})
                return f"Added {len(uris)} track(s) to playlist."

            case "remove_tracks":
                if not playlist_id or not track_ids:
                    return "Error: playlist_id and track_ids are required."
                uris = [
                    {"uri": f"spotify:track:{t}" if not t.startswith("spotify:") else t}
                    for t in track_ids
                ]
                await _delete(f"playlists/{playlist_id}/items", {"items": uris})
                return f"Removed {len(uris)} track(s) from playlist."

            case "change_details":
                if not playlist_id:
                    return "Error: playlist_id is required."
                body = {}
                if name:
                    body["name"] = name
                if description:
                    body["description"] = description
                await _put(f"playlists/{playlist_id}", body)
                return "Playlist details updated."

            case "create":
                if not name:
                    return "Error: name is required."
                data = await _post("me/playlists", {
                    "name": name,
                    "description": description or "",
                    "public": public,
                })
                return json.dumps({
                    "name": data["name"],
                    "id": data["id"],
                    "owner": data.get("owner", {}).get("display_name"),
                    "public": data.get("public"),
                }, indent=2)

            case "delete":
                if not playlist_id:
                    return "Error: playlist_id is required."
                await _delete(f"playlists/{playlist_id}/followers")
                return "Playlist deleted (unfollowed)."

            case _:
                return f"Unknown action: {action}"
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def spotify_liked_songs(
    action: str = Field(default="get", description="Action: 'get', 'get_with_genres', 'like', 'unlike', or 'check'"),
    limit: int = Field(default=0, description="Max songs to return (0 = all, for 'get'/'get_with_genres')"),
    track_ids: Optional[list[str]] = Field(default=None, description="Track IDs or URIs to like/unlike"),
) -> str:
    """Get user's liked/saved songs, or like/unlike tracks."""
    try:
        if action == "like":
            if not track_ids:
                return "Error: track_ids is required."
            uris = [f"spotify:track:{t}" if not t.startswith("spotify:") else t for t in track_ids]
            uris_param = ",".join(uris)
            await _api("PUT", f"me/library?uris={uris_param}")
            return f"Liked {len(uris)} track(s)."

        if action == "unlike":
            if not track_ids:
                return "Error: track_ids is required."
            uris = [f"spotify:track:{t}" if not t.startswith("spotify:") else t for t in track_ids]
            uris_param = ",".join(uris)
            await _api("DELETE", f"me/library?uris={uris_param}")
            return f"Unliked {len(uris)} track(s)."

        if action == "check":
            if not track_ids:
                return "Error: track_ids is required."
            uris = [f"spotify:track:{t}" if not t.startswith("spotify:") else t for t in track_ids]
            uris_param = ",".join(uris)
            data = await _get(f"me/library/contains", uris=uris_param)
            if not data:
                return "Error: could not check library."
            results = {uri: liked for uri, liked in zip(uris, data)}
            return json.dumps(results, indent=2)

        if action not in ("get", "get_with_genres"):
            return f"Unknown action: {action}"

        tracks = []
        offset = 0
        while True:
            data = await _get("me/tracks", limit=50, offset=offset)
            if not data or not data.get("items"):
                break
            for item in data["items"]:
                track = item.get("track")
                if not track:
                    continue
                info = _parse_track(track)
                info["added_at"] = item.get("added_at")
                info["artist_ids"] = [
                    a["id"] for a in track.get("artists", []) if a.get("id")
                ]
                tracks.append(info)
                if 0 < limit <= len(tracks):
                    break
            if 0 < limit <= len(tracks):
                tracks = tracks[:limit]
                break
            offset += 50
            if not data.get("next"):
                break

        if action == "get_with_genres":
            all_ids = {aid for t in tracks for aid in t.get("artist_ids", [])}
            sem = asyncio.Semaphore(10)

            async def fetch_genres(aid: str) -> tuple:
                async with sem:
                    try:
                        a = await _get(f"artists/{aid}")
                        return aid, a.get("genres", []) if a else []
                    except Exception:
                        return aid, []

            results = await asyncio.gather(*[fetch_genres(aid) for aid in all_ids])
            genres_map = dict(results)

            for t in tracks:
                t["genres"] = list({
                    g for aid in t.pop("artist_ids", [])
                    for g in genres_map.get(aid, [])
                })
        else:
            for t in tracks:
                t.pop("artist_ids", None)

        return json.dumps({"total": len(tracks), "tracks": tracks}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def spotify_recently_played(
    limit: int = Field(default=10, description="Number of recently played tracks to return (max 50)"),
) -> str:
    """Get the user's recently played tracks with timestamps."""
    try:
        limit = max(1, min(50, limit))
        data = await _get("me/player/recently-played", limit=limit)
        if not data or not data.get("items"):
            return "No recently played tracks found."
        tracks = []
        for item in data["items"]:
            track = _parse_track(item.get("track"))
            if track:
                track["played_at"] = item.get("played_at")
                tracks.append(track)
        return json.dumps({"total": len(tracks), "tracks": tracks}, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def spotify_devices(
    action: str = Field(description="Action: 'list' or 'transfer'"),
    device_id: Optional[str] = Field(
        default=None, description="Device ID to transfer playback to (for 'transfer' action)"
    ),
) -> str:
    """List available Spotify devices or transfer playback to a different device."""
    try:
        match action:
            case "list":
                data = await _get("me/player/devices")
                devices = data.get("devices", []) if data else []
                if not devices:
                    return "No devices found. Open Spotify on a device first."
                result = []
                for d in devices:
                    result.append({
                        "id": d["id"],
                        "name": d.get("name"),
                        "type": d.get("type"),
                        "is_active": d.get("is_active", False),
                        "volume_percent": d.get("volume_percent"),
                    })
                return json.dumps(result, indent=2)

            case "transfer":
                if not device_id:
                    return "Error: device_id is required. Use action 'list' to see available devices."
                await _put("me/player", {"device_ids": [device_id]})
                return f"Playback transferred to device {device_id}."

            case _:
                return f"Unknown action: {action}"
    except Exception as e:
        return _handle_error(e)


# ── OAuth Auth Flow ──────────────────────────────────────────────────────────
def run_auth():
    """Interactive OAuth flow. Run with: spotify-mcp --auth"""
    import webbrowser

    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set.")
        sys.exit(1)

    # Normalize localhost -> 127.0.0.1
    redirect = REDIRECT_URI
    parsed = urlparse(redirect)
    if parsed.hostname == "localhost":
        port_str = f":{parsed.port}" if parsed.port else ""
        redirect = parsed._replace(netloc=f"127.0.0.1{port_str}").geturl()

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": SCOPES,
    }
    auth_url = f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"

    print(f"Opening browser for Spotify authorization...")
    print(f"If it doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Start local callback server
    cb_parsed = urlparse(redirect)
    host = cb_parsed.hostname or "127.0.0.1"
    port = cb_parsed.port or 8080

    auth_code = None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                auth_code = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Authorization successful!</h1>"
                    b"<p>You can close this tab.</p>"
                )
            else:
                error = qs.get("error", ["unknown"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h1>Authorization failed: {error}</h1>".encode())

        def log_message(self, format, *args):
            pass

    server = HTTPServer((host, port), Handler)
    print(f"Waiting for callback on {host}:{port}...")
    server.handle_request()

    if not auth_code:
        print("Error: No authorization code received.")
        sys.exit(1)

    # Exchange code for token
    print("Exchanging code for token...")
    resp = httpx.post(SPOTIFY_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": redirect,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = time.time() + token.get("expires_in", 3600)
    _save_token(token)
    print(f"Token saved to {CACHE_PATH}")
    print("You can now use spotify-mcp with your MCP client.")
