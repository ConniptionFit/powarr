"""AD-29: YouTube Channel & Non-Music Detection System.

Detects and filters out non-music YouTube channels, podcasts, and creators
(e.g., Funhaus, astrogoblin, BroughtYouThisThing, PyroLIVE, Inside Games),
while shielding real musicians who operate YouTube channels (e.g. Joji,
Bo Burnham, Pomplamoose, Mouth Culture, VUKOVI, Billie Eilish) against
false positives.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_BLOCKED_CHANNELS: list[str] = [
    "Funhaus", "astrogoblin", "BroughtYouThisThing", "PyroLIVE", "Inside Games",
    "Dr Insanity", "Explore With Us", "Nexpo", "RedLetterMedia", "OneyPlays",
    "Internet Today", "Mythical Kitchen", "Corridor Crew", "Signified B Sides",
]

# Platforms that indicate genuine musical distribution
MUSIC_LINK_TYPES: set[str] = {
    "spotify", "apple", "itunes", "itun", "deezer", "bandcamp",
    "tidal", "qobuz", "soundcloud", "discogs", "allmusic", "beatport",
    "genius", "rateyourmusic", "songkick", "bandsintown",
}

# Musical genre tokens that indicate musical artistry
MUSIC_GENRE_KEYWORDS: set[str] = {
    "rock", "metal", "pop", "indie", "electronic", "hip hop", "hip-hop",
    "rap", "punk", "jazz", "folk", "rnb", "r&b", "ambient", "classical",
    "blues", "soul", "shoegaze", "house", "techno", "dance", "synthpop",
    "reggae", "ska", "country", "alternative", "emo", "hardcore",
    "instrumental", "acoustic", "lo-fi", "lofi", "soundtrack", "post-rock",
    "screamo", "math rock", "post-hardcore", "electronic rock",
}

# Tags commonly assigned to non-music YouTube channels/creators/podcasts
NON_MUSIC_TAGS: set[str] = {
    "youtube", "youtuber", "youtubers", "podcast", "podcasts", "gaming",
    "lets play", "let's play", "streamer", "streamers", "twitch", "vlog",
    "vlogs", "vlogger", "vloggers", "video essay", "comedy group",
    "comedy troupe", "talk show", "actual play", "audiobook", "commentary",
}

# Phrases in bio or disambiguation that signify non-music channels
NON_MUSIC_PHRASES: list[str] = [
    "youtube channel",
    "youtuber",
    "content creator",
    "gaming channel",
    "video podcast",
    "twitch streamer",
    "let's play",
    "division of rooster teeth",
    "entertainment company",
    "comedy group",
    "comedy troupe",
    "commentary youtuber",
]


def normalize_channel_name(name: str) -> str:
    """Normalize names for case- and whitespace-insensitive matching."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def is_name_in_blocked_channels(name: str, blocked_channels: list[str] | None) -> bool:
    """Check if an artist name matches the configured blocked channels list."""
    if not name or not blocked_channels:
        return False
    norm_name = normalize_channel_name(name)
    if not norm_name:
        return False
    for b in blocked_channels:
        if normalize_channel_name(b) == norm_name:
            return True
    return False


def has_music_platform_links(links: list[dict] | list[str] | None) -> bool:
    """Check if links from Lidarr or MusicBrainz contain recognized music platforms."""
    if not links:
        return False
    for link in links:
        link_name = ""
        if isinstance(link, dict):
            link_name = (link.get("name") or "").lower()
            url = (link.get("url") or "").lower()
            if any(p in url for p in MUSIC_LINK_TYPES):
                return True
        elif isinstance(link, str):
            link_name = link.lower()
        if link_name in MUSIC_LINK_TYPES:
            return True
    return False


def has_music_genres(genres: list[str] | None) -> bool:
    """Check if tags or genres contain recognized musical categories."""
    if not genres:
        return False
    for g in genres:
        g_clean = (g or "").lower()
        if any(kw in g_clean for kw in MUSIC_GENRE_KEYWORDS):
            return True
        for token in re.split(r"[\s\-_/]+", g_clean):
            if token in MUSIC_GENRE_KEYWORDS:
                return True
    return False


def has_non_music_tags(genres: list[str] | None) -> bool:
    """Check if tags contain non-music YouTube/creator/podcast tags."""
    if not genres:
        return False
    for g in genres:
        g_clean = (g or "").lower()
        if g_clean in NON_MUSIC_TAGS:
            return True
        for token in re.split(r"[\s\-_/]+", g_clean):
            if token in NON_MUSIC_TAGS:
                return True
    return False


def bio_indicates_non_music(bio: str | None) -> bool:
    """Check if artist bio or disambiguation contains non-music channel phrases."""
    if not bio:
        return False
    bio_lower = bio.lower()
    return any(phrase in bio_lower for phrase in NON_MUSIC_PHRASES)


def is_non_music_channel(
    name: str,
    *,
    mbid: str | None = None,
    tags: list[str] | None = None,
    bio: str | None = None,
    lidarr_artist_data: dict[str, Any] | None = None,
    in_library: bool = False,
    blocked_channels: list[str] | None = None,
) -> tuple[bool, str]:
    """Classify whether an entity is a non-music YouTube channel/podcast/creator.

    Returns:
        (is_non_music: bool, reason: str)

    Crucial false-positive shield:
    - If in_library is True, NEVER flagged as non-music channel.
    - If artist has music streaming links (Spotify, Apple, Deezer, etc.) or verified
      music genres, they are protected from being classified as a non-music channel,
      even if they operate an active YouTube channel.
    """
    # Shield 1: If user already owns this artist in Lidarr or Plex, it is a music artist
    if in_library:
        return False, "owned_in_library"

    # Match 1: Explicit user-configured blocked channels list
    if is_name_in_blocked_channels(name, blocked_channels):
        return True, "matches_blocked_channels"

    # Extract signals from Lidarr artist lookup data if provided
    links = (lidarr_artist_data.get("links") if lidarr_artist_data else None) or []
    lidarr_genres = (lidarr_artist_data.get("genres") if lidarr_artist_data else None) or []
    all_tags = list(tags or []) + list(lidarr_genres)
    disambiguation = (lidarr_artist_data.get("disambiguation") if lidarr_artist_data else "") or ""

    music_links = has_music_platform_links(links)
    music_genres = has_music_genres(all_tags)
    non_music_tags = has_non_music_tags(all_tags)
    bio_non_music = bio_indicates_non_music(bio) or bio_indicates_non_music(disambiguation)

    # Shield 2: Strong music signals protect musicians who also have YouTube presence
    if music_links and music_genres:
        return False, "verified_musician_links_and_genres"
    if music_links and not non_music_tags and not bio_non_music:
        return False, "verified_musician_platform_presence"

    # Flag 1: Bio or disambiguation specifically states it is a YouTube channel / creator
    if bio_non_music and not music_genres and not music_links:
        return True, "bio_indicates_youtube_or_podcast"

    # Flag 2: Has non-music tags (youtube, podcast, gaming) and no music genres
    if non_music_tags and not music_genres:
        return True, "non_music_tags"

    # Flag 3: Ghost MusicBrainz entry (has MBID but 0 music links, 0 music genres,
    # and either 0 albums in Lidarr or explicit non-music signals like Funhaus)
    if lidarr_artist_data:
        albums = lidarr_artist_data.get("albums") or []
        discogs_id = lidarr_artist_data.get("discogsId") or 0
        tadb_id = lidarr_artist_data.get("tadbId") or 0
        has_any_albums = len(albums) > 0 or bool(lidarr_artist_data.get("lastAlbum"))
        if not music_links and not music_genres and not has_any_albums and discogs_id == 0 and tadb_id == 0:
            if bio_non_music or non_music_tags or is_name_in_blocked_channels(name, blocked_channels):
                return True, "ghost_entry_no_music_data"

    return False, "clean"
