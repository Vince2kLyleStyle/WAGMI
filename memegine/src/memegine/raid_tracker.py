"""Track raider engagement on Twitter raids."""
from pathlib import Path
from datetime import datetime
import json
from .config import settings


TRACKER_FILE = settings.data_dir / "raids" / "raid_tracker.json"


def log_raider_action(user_id: int, username: str, action: str, tweet_url: str) -> None:
    """Log a raider's action (like, retweet, reply).

    Args:
        user_id: Telegram user ID
        username: Telegram username
        action: "like", "retweet", or "reply"
        tweet_url: The Twitter URL they engaged with
    """
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Load existing data
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE, 'r') as f:
            data = json.load(f)
    else:
        data = {"raids": []}

    # Add action log
    data["raids"].append({
        "user_id": user_id,
        "username": username,
        "action": action,
        "tweet_url": tweet_url,
        "timestamp": datetime.utcnow().isoformat(),
    })

    # Save
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def get_raider_stats(user_id: int = None) -> dict:
    """Get engagement stats for a raider or all raiders.

    Args:
        user_id: Optional user ID to filter by

    Returns:
        Dict with stats: {user_id: {"likes": N, "retweets": N, "replies": N, ...}}
    """
    if not TRACKER_FILE.exists():
        return {}

    with open(TRACKER_FILE, 'r') as f:
        data = json.load(f)

    stats = {}
    for raid in data.get("raids", []):
        uid = raid.get("user_id")
        if user_id and uid != user_id:
            continue

        if uid not in stats:
            stats[uid] = {
                "username": raid.get("username"),
                "likes": 0,
                "retweets": 0,
                "replies": 0,
                "total": 0,
            }

        action = raid.get("action", "")
        if action == "like":
            stats[uid]["likes"] += 1
        elif action == "retweet":
            stats[uid]["retweets"] += 1
        elif action == "reply":
            stats[uid]["replies"] += 1
        stats[uid]["total"] += 1

    return stats


def get_leaderboard(limit: int = 10) -> list:
    """Get top raiders by engagement count.

    Returns:
        List of (user_id, username, total_count) sorted by engagement
    """
    stats = get_raider_stats()

    leaderboard = [
        (uid, data["username"], data["total"])
        for uid, data in stats.items()
    ]

    leaderboard.sort(key=lambda x: x[2], reverse=True)
    return leaderboard[:limit]
