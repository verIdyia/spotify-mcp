# spotify-mcp

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects Claude with Spotify. Control your Spotify playback, search music, manage playlists, and more -- all through natural conversation with Claude.

> **Fork Notice**: This project is forked from [varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp) and updated for the **Spotify Web API February 2026 changes**. Built and maintained with [Claude Code](https://claude.ai/claude-code).

## What's Changed (v0.3.0)

### Spotify API Feb 2026 Adaptation
- **Search limit**: Default reduced to 5, max capped at 10 (Spotify Dev Mode restriction)
- **Playlist endpoints**: Migrated from `/tracks` to `/items` (new API requirement)
- **Response field compatibility**: Handles both old (`tracks`/`track`) and new (`items`/`item`) field names
- **Batch endpoints removed**: Artist genre lookups now use parallel individual fetches via `ThreadPoolExecutor`
- **Playlist creation**: Uses `POST /me/playlists` instead of deprecated `POST /users/{id}/playlists`

### Bug Fixes & Improvements
- **New**: Playlist `delete` action (unfollow/delete playlists)
- **Fixed**: Lazy client initialization -- no longer crashes at import time without credentials
- **Fixed**: `assert` crash replaced with proper error handling for unknown tool names
- **Fixed**: `change_playlist_details()` now returns the API response
- **Fixed**: `get_liked_songs()` limit edge case -- stops fetching as soon as limit is reached
- **Fixed**: Null track handling in liked songs pagination
- **Improved**: Search default limit aligned with Spotify's new default (5)
- **Improved**: `.gitignore` expanded to cover more edge cases (`.cache-*`, `.env.*`, build artifacts)
- **Security**: OAuth token cache (`.cache`) excluded from repository

### Original Features (from upstream)
- Start, pause, and skip playback
- Search for tracks, albums, artists, and playlists
- Get detailed info about any Spotify item
- Manage the playback queue
- Full playlist CRUD (create, read, update, delete tracks)
- Retrieve liked/saved songs with optional genre enrichment

## How It Works

```
Claude <--MCP (stdio)--> spotify-mcp server <--REST API--> Spotify Web API
```

1. Claude sends tool calls (e.g., `SpotifySearch`, `SpotifyPlayback`) via the MCP protocol
2. The MCP server receives these calls and translates them into Spotify Web API requests
3. Authentication is handled via OAuth 2.0 (Authorization Code Flow) with automatic token refresh
4. Results are parsed into clean, concise JSON and returned to Claude

### Available Tools

| Tool | Actions | Description |
|------|---------|-------------|
| `SpotifyPlayback` | `get`, `start`, `pause`, `skip` | Control music playback |
| `SpotifySearch` | -- | Search tracks, albums, artists, playlists |
| `SpotifyQueue` | `get`, `add` | View and manage play queue |
| `SpotifyGetInfo` | -- | Get detailed item info by Spotify URI |
| `SpotifyPlaylist` | `get`, `get_tracks`, `add_tracks`, `remove_tracks`, `change_details`, `create`, `delete` | Full playlist management |
| `SpotifyLikedSongs` | `get`, `get_with_genres` | Retrieve saved songs with optional genres |

### Architecture

```
src/spotify_mcp/
  __init__.py       # Package entry point
  server.py         # MCP server - tool definitions & request routing
  spotify_api.py    # Spotify API client wrapper (auth, playback, search, playlists)
  utils.py          # Response parsers & decorators (validate, ensure_username)
```

## Prerequisites

- **Python 3.12+**
- **Spotify Premium** account (required for Dev Mode API access since Feb 2026)
- **Spotify Developer App** credentials

## Configuration

### 1. Create Spotify Developer App

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Set redirect URI to `http://127.0.0.1:8080/callback`
4. Note your **Client ID** and **Client Secret**

> **Important (Feb 2026)**: Dev Mode apps are limited to 5 authorized users and require the app owner to have Spotify Premium.

### 2. Set Up Environment

Copy the example environment file and fill in your credentials:

```bash
cp .env.example .env
# Edit .env with your SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI
```

### 3. Add to MCP Client

#### Run locally (recommended)

```bash
git clone https://github.com/verIdyia/spotify-mcp.git
```

Add to your MCP config (Claude Desktop, Cursor, etc.):

```json
{
  "mcpServers": {
    "spotify": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/spotify-mcp",
        "run",
        "spotify-mcp"
      ],
      "env": {
        "SPOTIFY_CLIENT_ID": "your_client_id",
        "SPOTIFY_CLIENT_SECRET": "your_client_secret",
        "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8080/callback"
      }
    }
  }
}
```

#### Run with uvx

```json
{
  "mcpServers": {
    "spotify": {
      "command": "uvx",
      "args": [
        "--python", "3.12",
        "--from", "git+https://github.com/verIdyia/spotify-mcp",
        "spotify-mcp"
      ],
      "env": {
        "SPOTIFY_CLIENT_ID": "your_client_id",
        "SPOTIFY_CLIENT_SECRET": "your_client_secret",
        "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8080/callback"
      }
    }
  }
}
```

### Config File Locations

- **Claude Desktop (macOS)**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Claude Desktop (Windows)**: `%APPDATA%/Claude/claude_desktop_config.json`

## Troubleshooting

1. **Make sure `uv` is updated** -- version `>=0.54` recommended
2. **First run OAuth**: On the first tool call, a browser window will open for Spotify login. After authorizing, the token is cached locally in `.cache`
3. **No active device error**: Make sure Spotify is open and playing on at least one device
4. **Permission errors on Linux/Mac**: `chmod -R 755 /path/to/spotify-mcp`

### Debugging

Launch the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/spotify-mcp run spotify-mcp
```

Logs are emitted to stderr. On Mac, Claude Desktop logs are at `~/Library/Logs/Claude`.

## Spotify API Feb 2026 Changes Summary

Key changes that affected this MCP:

| Change | Impact | Status |
|--------|--------|--------|
| Search `limit` max reduced 50 -> 10 | Fewer results per search | Adapted |
| Playlist endpoints `/tracks` -> `/items` | Endpoint URLs changed | Adapted |
| Response field `tracks` -> `items` | Parsing updated | Adapted |
| Batch GET endpoints removed | Must fetch individually | Adapted |
| `popularity` field removed from tracks | No longer available | Handled |
| Dev Mode: 5 user limit, Premium required | Access restriction | Documented |

For full details, see [Spotify's migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).

## Credits

- **Original project**: [varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp) by [Varun Srivastava](https://github.com/varunneal) (MIT License)
- **Original contributors**: @jamiew, @davidpadbury, @manncodes, @hyuma7, @aanurraj, @JJGO and others
- **Built with**: [Spotipy](https://github.com/spotipy-dev/spotipy) 2.24.0, [MCP SDK](https://github.com/modelcontextprotocol/python-sdk) 1.3.0
- **Maintained with**: [Claude Code](https://claude.ai/claude-code) (Anthropic)

## License

MIT License -- see [LICENSE](LICENSE) for details.
