# Changelog

## [0.3.0] - 2026-02-28

### Spotify API Feb 2026 Migration
- Adapted search limit to max 10 (Spotify Dev Mode restriction)
- Migrated playlist endpoints from `/tracks` to `/items`
- Added response field compatibility for both old and new API formats
- Replaced removed batch artist endpoint with parallel individual fetches
- Updated playlist creation to use `POST /me/playlists`

### New Features
- Added playlist `delete` action (unfollow/delete via `DELETE /playlists/{id}/followers`)
- Lazy client initialization -- server starts without credentials, client created on first tool call

### Bug Fixes
- Replaced `assert` with proper error handling for unknown tool names
- Fixed `change_playlist_details()` missing return value
- Fixed `get_liked_songs()` limit edge case (stops immediately when limit reached)
- Added null track handling in liked songs pagination

### Improvements
- Aligned search default limit with Spotify's new default (5)
- Expanded `.gitignore` for better coverage
- Removed Korean comments for international audience
- Added `.env.example` for easier setup
- Updated documentation with Feb 2026 API changes

### Security
- Ensured OAuth token cache is excluded from repository
- No credentials or tokens in committed files

## [0.2.0] - Upstream

Original release from [varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp).
See upstream repository for full history.
