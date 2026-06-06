import logging
from datetime import datetime, timedelta, timezone

import requests

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class HockeyNightBoard(BasePlugin):
    def __init__(self, plugin):
        super().__init__(plugin)
        self.plugin = plugin

    def _get_settings_template_params(self, plugin_settings):
        plugin_settings = plugin_settings or {}
        return {
            "plugin_settings": plugin_settings,
            "style_settings": True,
            "title": plugin_settings.get("title", ""),
            "nhlTeam": plugin_settings.get("nhlTeam", "MTL"),
        }

    def generate_image(self, settings, device_config):
        template_params = self.get_template_context(settings, device_config)

        try:
            width, height = device_config.get_resolution()
        except Exception as e:
            raise RuntimeError(f"Failed to get display resolution: {e}")

        return self.render_image(
            dimensions=(width, height),
            html_file="nhl_team_schedule.html",
            css_file="nhl_team_schedule.css",
            template_params=template_params,
        )

    def get_template_context(self, settings, device_config):
        settings = settings or {}
        team_code = str(settings.get("nhlTeam", "MTL")).strip().upper()
        custom_title = str(settings.get("title", "")).strip()
        now_utc = datetime.now(timezone.utc)

        context = {
            "plugin_settings": settings,
            "style_settings": True,
            "title": custom_title,
            "nhl_team_code": team_code,
            "display_mode": "next",
            "display_label": "Today",
            "display_time": "TBD",
            "time": "TBD",
            "day": "Today",
            "game_mode": "next",
            "networks": [],
            "venue": "",
            "away_team": {},
            "home_team": {},
            "away_team_logo": "",
            "home_team_logo": "",
            "away_team_stats": {
                "wins": 0,
                "losses": 0,
                "otLosses": 0,
                "goalFor": "N/A",
                "goalAgainst": "N/A",
                "powerPlayPct": 0,
                "penaltyKillPct": 0,
            },
            "home_team_stats": {
                "wins": 0,
                "losses": 0,
                "otLosses": 0,
                "goalFor": "N/A",
                "goalAgainst": "N/A",
                "powerPlayPct": 0,
                "penaltyKillPct": 0,
            },
            "error": None,
        }

        try:
            selected_game = self._get_preferred_game_for_team(team_code, now_utc)
            if not selected_game:
                context["error"] = f"No upcoming games found for {team_code}"
                return context

            is_last_game = self._is_last_game(selected_game, now_utc)
            game_context = self._build_game_context(selected_game, is_last_game)

            away_abbrev = ((game_context["away_team"] or {}).get("abbrev") or "").upper()
            home_abbrev = ((game_context["home_team"] or {}).get("abbrev") or "").upper()

            game_context["away_team_logo"] = self._logo_filename(away_abbrev)
            game_context["home_team_logo"] = self._logo_filename(home_abbrev)
            game_context["away_team_stats"] = self._get_team_standings_stats(away_abbrev)
            game_context["home_team_stats"] = self._get_team_standings_stats(home_abbrev)

            if not custom_title:
                if is_last_game:
                    game_context["title"] = "Last Game"
                elif game_context.get("day") == "Today":
                    game_context["title"] = "Today's Matchup"
                else:
                    game_context["title"] = f"{game_context.get('day', 'Upcoming')} Matchup"

            game_context["plugin_settings"] = settings
            game_context["style_settings"] = True

            context.update(game_context)
            return context

        except requests.RequestException as exc:
            logger.exception("NHL Team Schedule request error")
            context["error"] = f"NHL schedule could not be loaded"
            return context
        except Exception as exc:
            logger.exception("NHL Team Schedule unexpected error")
            context["error"] = "Unable to load NHL schedule"
            return context

    def _request_json(self, url):
        response = requests.get(url, timeout=(5, 20))
        response.raise_for_status()
        return response.json()

    def _extract_games_from_schedule(self, data):
        if isinstance(data.get("games"), list):
            return data.get("games", [])

        club_schedule = data.get("clubSchedule") or {}
        if isinstance(club_schedule.get("games"), list):
            return club_schedule.get("games", [])

        if isinstance(data.get("gameWeek"), list):
            games = []
            for week in data.get("gameWeek", []):
                for game in week.get("games", []) or []:
                    games.append(game)
            return games

        return []

    def _game_start_dt(self, game):
        start_utc = game.get("startTimeUTC")
        if not start_utc:
            return None
        try:
            return datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        except Exception:
            return None

    def _get_preferred_game_for_team(self, team_code, now_utc):
        start_date = (now_utc - timedelta(days=3)).strftime("%Y-%m-%d")
        data = self._request_json(f"https://api-web.nhle.com/v1/club-schedule/{team_code}/week/{start_date}")

        games = self._extract_games_from_schedule(data)
        if not games:
            return None

        future_games = []
        past_games = []

        for game in games:
            start_dt = self._game_start_dt(game)
            if not start_dt:
                continue

            game_state = (game.get("gameState") or game.get("gameScheduleState") or "").upper()

            if game_state in {"OFF", "FINAL"}:
                past_games.append((start_dt, game))
                continue

            if start_dt >= now_utc:
                future_games.append((start_dt, game))
            else:
                past_games.append((start_dt, game))

        future_games.sort(key=lambda item: item[0])
        past_games.sort(key=lambda item: item[0], reverse=True)

        if future_games:
            return future_games[0][1]
        if past_games:
            return past_games[0][1]
        return games[0]

    def _is_last_game(self, game, now_utc):
        start_dt = self._game_start_dt(game)
        if not start_dt:
            return False

        game_state = (game.get("gameState") or game.get("gameScheduleState") or "").upper()
        if game_state in {"OFF", "FINAL"}:
            return True

        return start_dt < now_utc

    def _team_from_game_side(self, side):
        side = side or {}
        abbrev = side.get("abbrev")
        if isinstance(abbrev, dict):
            abbrev = abbrev.get("default") or ""

        return {
            "id": side.get("id"),
            "abbrev": abbrev or "",
            "commonName": side.get("commonName") or {},
            "placeName": side.get("placeName") or {},
        }

    def _build_game_context(self, game, is_last_game=False):
        away_team = self._team_from_game_side(game.get("awayTeam"))
        home_team = self._team_from_game_side(game.get("homeTeam"))

        start_dt = self._game_start_dt(game)
        local_dt = start_dt.astimezone() if start_dt else None

        if local_dt:
            display_time = local_dt.strftime("%-I:%M %p")
            local_today = datetime.now().astimezone().date()
            day_label = "Today" if local_dt.date() == local_today else local_dt.strftime("%A")
        else:
            display_time = "TBD"
            day_label = "Upcoming"

        venue_name = (
            ((game.get("venue") or {}).get("default"))
            or ((home_team.get("placeName") or {}).get("default"))
            or ""
        )

        networks = []
        for item in game.get("tvBroadcasts", []) or []:
            name = item.get("network") or item.get("market") or item.get("countryCode")
            if name and name not in networks:
                networks.append(name)

        return {
            "display_mode": "previous" if is_last_game else "next",
            "display_label": "Last Game" if is_last_game else day_label,
            "display_time": display_time,
            "time": display_time,
            "day": day_label,
            "game_mode": "last" if is_last_game else "next",
            "networks": networks,
            "venue": venue_name,
            "away_team": away_team,
            "home_team": home_team,
        }

    def _get_team_standings_stats(self, team_abbrev):
        if not team_abbrev:
            return {
                "wins": 0,
                "losses": 0,
                "otLosses": 0,
                "goalFor": "N/A",
                "goalAgainst": "N/A",
                "powerPlayPct": 0,
                "penaltyKillPct": 0,
            }

        data = self._request_json("https://api-web.nhle.com/v1/standings/now")

        for team in data.get("standings", []):
            abbrev = (((team.get("teamAbbrev") or {}).get("default")) or "").upper()
            if abbrev != team_abbrev.upper():
                continue

            return {
                "wins": team.get("wins", 0),
                "losses": team.get("losses", 0),
                "otLosses": team.get("otLosses", 0),
                "goalFor": team.get("goalFor", team.get("goalsFor", "N/A")),
                "goalAgainst": team.get("goalAgainst", team.get("goalsAgainst", "N/A")),
                "powerPlayPct": team.get("powerPlayPct", team.get("powerPlayPercentage", 0)),
                "penaltyKillPct": team.get("penaltyKillPct", team.get("penaltyKillPercentage", 0)),
            }

        return {
            "wins": 0,
            "losses": 0,
            "otLosses": 0,
            "goalFor": "N/A",
            "goalAgainst": "N/A",
            "powerPlayPct": 0,
            "penaltyKillPct": 0,
        }

    def _logo_filename(self, team_abbrev):
        return f"{team_abbrev.lower()}.png" if team_abbrev else ""