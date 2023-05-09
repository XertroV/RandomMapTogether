import asyncio
import logging
import time as py_time
from threading import Thread

from pyplanet.apps.core.maniaplanet.models import Player
from pyplanet.contrib.chat import ChatManager
from pyplanet.contrib.mode import ModeManager
from pyplanet.core.ui import GlobalUIManager
import pyplanet.conf

from . import MapHandler
from .Data.Configurations import RMCConfig, RMSConfig
from .Data.GameScore import GameScore
from .Data.GameState import GameState
from .Data.Medals import Medals, max_medal, min_medal
from .Data.GameModes import GameModes

from .views import RandomMapsTogetherView, RMTScoreBoard

BIG_MESSAGE = 'Race_BigMessage'

RACE_SCORES_TABLE = 'Race_ScoresTable'

S_TIME_LIMIT = 'S_TimeLimit'
S_FORCE_LAPS_NB = 'S_ForceLapsNb'
S_CHAT_TIME = 'S_ChatTime'
_lock = asyncio.Lock()

# pyplanet.conf.settings.DEBUG = True

logger = logging.getLogger(__name__)


async def background_loading_map(map_handler: MapHandler):
    logger.info("[background_loading_map] STARTED")
    await map_handler.pre_load_next_map()
    logger.info("[background_loading_map] COMPLETED")


class RMTGame:
    def __init__(self, app, map_handler: MapHandler, chat: ChatManager, mode_manager: ModeManager,
                 score_ui: RandomMapsTogetherView, tm_ui_manager: GlobalUIManager):
        self.app = app
        self._rmt_starter_player: Player = None
        self._score = GameScore()
        self._map_handler = map_handler
        self._chat = chat
        self._mode_manager = mode_manager
        self._mode_settings = None
        self._map_start_time = py_time.time()
        self._time_left = self.app.app_settings.game_time_seconds
        self._score_ui = score_ui
        self._game_state = GameState()
        self._score_ui.set_score(self._score)
        self._score_ui.set_game_state(self._game_state)
        self._scoreboard_ui = RMTScoreBoard(self._score_ui.app, self._score, self._game_state, self)
        self._tm_ui = tm_ui_manager
        self._time_left_at_pause = 83
        self._time_at_pause = py_time.time()

        logger.info("RMT Game initialized")

    async def on_init(self):
        # await self._map_handler.load_hub()
        logger.info("RMT Game loaded")
        self._mode_settings = await self._mode_manager.get_settings()
        self._mode_settings[S_FORCE_LAPS_NB] = int(-1)
        asyncio.create_task(background_loading_map(self._map_handler))
        # self._mode_settings[S_CHAT_TIME] = int(1)
        await self.hide_timer()
        await self._score_ui.display()
        self._score_ui.subscribe("ui_start_rmt", self.command_start_rmt)
        self._score_ui.subscribe("ui_stop_rmt", self.command_stop_rmt)
        self._score_ui.subscribe("ui_skip_medal", self.command_skip_medal)
        self._score_ui.subscribe("ui_free_skip", self.command_free_skip)
        self._score_ui.subscribe("ui_toggle_pause", self.command_toggle_pause)
        # self._score_ui.subscribe("ui_toggle_scoreboard", self.command_toggle_scoreboard)

        self._score_ui.subscribe("ui_set_game_time_15m", self.set_game_time_15m)
        self._score_ui.subscribe("ui_set_game_time_30m", self.set_game_time_30m)
        self._score_ui.subscribe("ui_set_game_time_1h", self.set_game_time_1h)
        self._score_ui.subscribe("ui_set_game_time_2h", self.set_game_time_2h)

        self._score_ui.subscribe("ui_set_goal_bonus_1m", self.set_goal_bonus_1m)
        self._score_ui.subscribe("ui_set_goal_bonus_3m", self.set_goal_bonus_3m)
        self._score_ui.subscribe("ui_set_goal_bonus_5m", self.set_goal_bonus_5m)

        self._score_ui.subscribe("ui_set_skip_penalty_30s", self.set_skip_penalty_30s)
        self._score_ui.subscribe("ui_set_skip_penalty_1m", self.set_skip_penalty_1m)
        self._score_ui.subscribe("ui_set_skip_penalty_2m", self.set_skip_penalty_2m)

        self._score_ui.subscribe("ui_set_goal_medal_author", self.set_goal_medal_author)
        self._score_ui.subscribe("ui_set_goal_medal_gold", self.set_goal_medal_gold)
        self._score_ui.subscribe("ui_set_goal_medal_silver", self.set_goal_medal_silver)

        self._score_ui.subscribe("ui_set_skip_medal_gold", self.set_skip_medal_gold)
        self._score_ui.subscribe("ui_set_skip_medal_silver", self.set_skip_medal_silver)
        self._score_ui.subscribe("ui_set_skip_medal_bronze", self.set_skip_medal_bronze)

        self._score_ui.subscribe("ui_toggle_infinite_skips", self.toggle_infinite_skips)
        self._score_ui.subscribe("ui_toggle_admin_fins_only", self.toggle_admin_fins_only)
        self._score_ui.subscribe("ui_toggle_allow_pausing", self.toggle_allow_pausing)

        self._score_ui.subscribe("ui_set_game_mode_rmc", self.set_game_mode_rmc)
        self._score_ui.subscribe("ui_set_game_mode_rms", self.set_game_mode_rms)

        asyncio.ensure_future(self.update_ui_loop())

    async def update_ui_loop(self):
        while True:
            await asyncio.sleep(0.25)
            if len(self._scoreboard_ui._is_player_shown) > 0:
                await self._scoreboard_ui.display(self._scoreboard_ui._is_player_shown.keys())

    async def command_start_rmt(self, player: Player, _, values, *args, **kwargs):
        if player.level < self.app.app_settings.min_level_to_start:
            await self._chat("You are not allowed to start the game", player)
            return

        # if self._map_handler is None or self._map_handler.active_map is None or self._map_handler.active_map.uid != self._map_handler._hub_map:
        #     await self._chat("Please wait for the hub map to load", player)
        #     return
        await self._score_ui.hide()
        await self._scoreboard_ui.display()

        if self._game_state.is_hub_stage():
            self.app.app_settings.game_time_seconds = int(values["game_time_seconds"])
            if self.app.app_settings.game_mode == GameModes.RANDOM_MAP_SURVIVAL:
                self.app.app_settings.goal_bonus_seconds = int(values["goal_bonus_seconds"])
                self.app.app_settings.skip_penalty_seconds = int(values["skip_penalty_seconds"])

            self._game_state.set_start_new_state()
            self._game_state.map_is_loading = True
            self._game_state.game_is_in_progress = True
            await self._score_ui.display()
            await self._chat(f'{player.nickname} started new RMT, loading next map ...')
            self._rmt_starter_player = player
            self._time_left = self.app.app_settings.game_time_seconds
            self._mode_settings[S_TIME_LIMIT] = self._time_left

            if self.app.app_settings.game_mode == GameModes.RANDOM_MAP_CHALLENGE:
                if self.app.app_settings.admin_fins_only:
                    self._game_state.set_finishes_player_filter(player.login, player.nickname)

            self._score.get_players_score(player)

            await self._score_ui.display()
            await self._scoreboard_ui.display()

            self._game_state.set_map_completed_state()

            async def _load_init_map():
                if await self.load_with_retry():
                    logger.info("RMT started")
                else:
                    self._game_state.set_hub_state()
                    self._mode_settings[S_TIME_LIMIT] = 0
                    await self._chat("RMT failed to start")
                await self._mode_manager.update_settings(self._mode_settings)
            await _load_init_map()
        else:
            await self._chat("RMT already started", player)

        await self._scoreboard_ui.hide()
        await self._score_ui.display()

    async def load_with_retry(self, max_retry=3) -> bool:
        retry = 0
        load_succeeded = False
        self._game_state.map_is_loading = True
        await self._score_ui.display()
        while not load_succeeded and retry < max_retry:
            retry += 1
            try:
                await self._map_handler.load_next_map()
                load_succeeded = True
            except Exception as e:
                logger.error("failed to load map...", exc_info=e)
                try:
                    await self._map_handler.remove_loaded_map()
                except Exception as e:
                    logger.error("failed to remove map...", exc_info=e)

        self._game_state.map_is_loading = False

        # await self._score_ui.hide()
        return retry < max_retry

    async def command_stop_rmt(self, player: Player, *args, **kwargs):
        if self._game_state.is_game_stage():
            if await self._is_player_allowed_to_manage_running_game(player):
                await self._chat(f'{player.nickname} stopped the current session')
                await self.back_to_hub()
            else:
                await self._chat("you can't stop the RMT", player)
        else:
            await self._chat("RMT is not started yet", player)

    async def back_to_hub(self):
        if self._game_state.is_game_stage():
            self._chat('Game over -- Returning to HUB')
            logger.info("Back to HUB ...")
            # self._time_left = 0
            await self.hide_timer()
            self._game_state.set_hub_state()
            await self._scoreboard_ui.display()
            self._score.rest()
            await self._score_ui.hide()
            await self._map_handler.remove_loaded_map()
            await self._map_handler.load_hub()
            self._rmt_starter_player = None
            logger.info("Back to HUB completed")

    async def map_begin_event(self, map, *args, **kwargs):
        logger.info("[map_begin_event] STARTED")
        self._map_handler.active_map = map
        self._score_ui.ui_controls_visible = True
        if self._game_state.is_game_stage():
            if self._map_handler.pre_patch_ice:
                await self._chat("$o$FB0 this track was created before the ICE physics change $z"
                                 , self._rmt_starter_player)
            self._game_state.set_new_map_in_game_state()
            asyncio.create_task(background_loading_map(self._map_handler))
        else:
            await self.hide_timer()
            self._game_state.current_map_completed = True

        await self._score_ui.display()
        logger.info("[map_begin_event] ENDED")

    async def map_end_event(self, time, count, *args, **kwargs):
        logger.info("MAP end")
        await self.set_original_scoreboard_visible(False)
        if self._game_state.is_game_stage():
            self._game_state.skip_medal_available = False
            self._game_state.skip_medal_player = None
            self._score_ui.ui_controls_visible = False
            if not self._game_state.current_map_completed:
                logger.info("RMT finished successfully")
                await self._chat(
                    f'Challenge completed {self.app.app_settings.goal_medal.value}: {self._score.total_goal_medals} {self.app.app_settings.skip_medal.value}: {self._score.total_skip_medals}')
                await self.back_to_hub()
            else:
                self._mode_settings[S_TIME_LIMIT] = self._time_left
                logger.info("Continue with %d time left", self._time_left)
                await self._mode_manager.update_settings(self._mode_settings)


    async def on_map_finsh(self, player: Player, race_time: int, lap_time: int, cps, lap_cps, race_cps, flow,
                           is_end_race: bool, is_end_lap, raw, *args, **kwargs):
        logger.info(f'[on_map_finsh] {player.nickname} has finished the map with time: {race_time}ms')
        if self._game_state.is_game_stage():
            await _lock.acquire()  # lock to avoid multiple AT before next map is loaded
            if self._game_state.current_map_completed:
                logger.info(f'[on_map_finish] Map was already completed')
                _lock.release()
                return

            if is_end_race:
                logger.info(f'[on_map_finish] Final time check for {self.app.app_settings.goal_medal.value}')

                time_counts = self._game_state.fins_count_from in ["*", player.login]
                if time_counts:
                    if self._game_state.is_paused:
                        _lock.release()
                        return await self._chat("Time doesn't count because game is paused", player)
                    if (py_time.time() - (race_time * 0.001)) < self._time_at_pause:
                        _lock.release()
                        return await self._chat(f"Time doesn't count because game was paused ({race_time}ms / cur time: {py_time.time()} / paused at time: {self._time_at_pause})", player)
                    if race_time <= self._map_handler.goal_medal:
                        logger.info(f'[on_map_finish {self.app.app_settings.goal_medal.value} acquired')
                        self.app.app_settings.update_time_left(self, goal_medal=True)
                        self._game_state.set_map_completed_state()
                        await self.hide_timer()
                        _lock.release()  # with loading True don't need to lock
                        await self._chat(f'{self.app.app_settings.goal_medal.value}, congratulations to {player.nickname}')
                        self._score.inc_goal_medal_count(player, self._map_handler.active_map, True)
                        await self._scoreboard_ui.display()
                        await self._score_ui.hide()
                        if not await self.load_with_retry():
                            await self.back_to_hub()
                    elif race_time <= self._map_handler.skip_medal and not self._game_state.skip_medal_available:
                        logger.info(f'[on_map_finish] {self.app.app_settings.skip_medal.value} acquired')
                        self._game_state.skip_medal_available = True
                        self._game_state.skip_medal_player = player
                        _lock.release()
                        await self._score_ui.display()
                        await self._chat(f'first {self.app.app_settings.skip_medal.value} acquired, congrats to {player.nickname}')
                        await self._chat(f'You are now allowed to Take the {self.app.app_settings.skip_medal.value} and skip the map', self._rmt_starter_player)
                    else:
                        _lock.release()
                else:
                    if race_time <= self._map_handler.goal_medal:
                        logger.info(f'[on_map_finish] {self.app.app_settings.goal_medal.value} acquired by player but it doesn\'t count')
                        if self._score.inc_goal_medal_count(player, self._map_handler.active_map, False):
                            await self._chat(f'{player.nickname} got {self.app.app_settings.goal_medal.value}. Gz but it doesn\'t count Sadge')
                    elif race_time <= self._map_handler.skip_medal:
                        self._score.inc_skip_medal_count(player, self._map_handler.active_map, False)
                    _lock.release()
            else:
                _lock.release()

    async def command_skip_medal(self, player: Player, *args, **kwargs):
        if self._game_state.is_paused:
            return await self._chat("Game currently paused", player)
        if self._game_state.skip_command_allowed():
            if self._game_state.skip_medal_available:
                if await self._is_player_allowed_to_manage_running_game(player):
                    self.app.app_settings.update_time_left(self, skip_medal=True)
                    self._score.inc_skip_medal_count(self._game_state.skip_medal_player, self._map_handler.active_map, True)
                    self._game_state.set_map_completed_state()
                    await self._chat(f'{player.nickname} decided to {self.app.app_settings.skip_medal.value} skip')
                    await self.hide_timer()
                    if await self.load_with_retry():
                        logging.info(f"Loading next map success")
                        await self._scoreboard_ui.display()
                        await self._score_ui.hide()
                        logging.info(f"Loading next map UI updated")
                    else:
                        await self.back_to_hub()
            else:
                await self._chat(f"{self.app.app_settings.skip_medal.value} skip is not available", player)
        else:
            await self._chat("You are not allowed to skip", player)

    async def command_free_skip(self, player: Player, *args, **kwargs):
        if self._game_state.is_paused:
            return await self._chat("Game currently paused", player)
        if self._game_state.skip_command_allowed():
            if self.app.app_settings.can_skip_map(self):
                if await self._is_player_allowed_to_manage_running_game(player):
                    self.app.app_settings.update_time_left(self, free_skip=True)
                    self._game_state.set_map_completed_state()
                    if not self._map_handler.pre_patch_ice and self._game_state.free_skip_available:
                        await self._chat(f'{player.nickname} decided to use free skip')
                        self._game_state.free_skip_available = False
                    await self._chat(f'{player.nickname} decided to skip the map')
                    await self.hide_timer()
                    await self._scoreboard_ui.display()
                    await self._score_ui.hide()
                    if not await self.load_with_retry():
                        await self.back_to_hub()
                    else:
                        logging.info(f"Skipping map success")
            else:
                await self._chat("Free skip is not available", player)
        else:
            await self._chat("You are not allowed to skip", player)

    async def command_toggle_pause(self, player: Player, *args, **kwargs):
        if not self.app.app_settings.allow_pausing or not await self._is_player_allowed_to_manage_running_game(player):
            return await self._chat("Cannot toggle pause", player)
        self._game_state.is_paused = not self._game_state.is_paused
        pause_duration = 0
        if self._game_state.is_paused:
            self._time_at_pause = py_time.time()
            self._time_left_at_pause = self._time_left
            self._mode_settings[S_TIME_LIMIT] = -1
        else:
            pause_duration = int(py_time.time() - self._time_at_pause + .5)
            self._mode_settings[S_TIME_LIMIT] = self._time_left_at_pause + pause_duration
            self._time_left += pause_duration
            # respawn the player, this means the unpausing player's next run always starts after unpausing.
            await self.respawn_player(player)
        await self._mode_manager.update_settings(self._mode_settings)
        await self._score_ui.display()
        # no need to extend b/c this is done by setting the time limit to whatever it was + pause duration
        # if pause_duration > 0:
        #     await self._map_handler._map_manager.extend_ta(pause_duration)
        logging.info(f"Set paused: " + str(self._game_state.is_paused))

    # async def command_toggle_scoreboard(self, player: Player, *args, **kw):
    #     await self._scoreboard_ui.toggle_for(player.login)

    async def respawn_player(self, player: Player):
        # first, force mode 1 (spectator), then force mode 2 (player), then force mode 0 (user selectable)
        await self._mode_manager._instance.gbx('ForceSpectator', player.login, 1)
        await self._mode_manager._instance.gbx('ForceSpectator', player.login, 2)
        await self._mode_manager._instance.gbx('ForceSpectator', player.login, 0)

    async def set_goal_bonus_1m(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.goal_bonus_seconds = 60
            await self._score_ui.display()

    async def set_goal_bonus_3m(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.goal_bonus_seconds = 180
            await self._score_ui.display()

    async def set_goal_bonus_5m(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.goal_bonus_seconds = 300
            await self._score_ui.display()

    async def set_skip_penalty_30s(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.skip_penalty_seconds = 30
            await self._score_ui.display()

    async def set_skip_penalty_1m(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.skip_penalty_seconds = 60
            await self._score_ui.display()

    async def set_skip_penalty_2m(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.skip_penalty_seconds = 120
            await self._score_ui.display()

    async def set_game_time_15m(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.game_time_seconds = 900
            await self._score_ui.display()

    async def set_game_time_30m(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.game_time_seconds = 1800
            await self._score_ui.display()

    async def set_game_time_1h(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.game_time_seconds = 3600
            await self._score_ui.display()

    async def set_game_time_2h(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.game_time_seconds = 7200
            await self._score_ui.display()

    async def set_goal_medal_author(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.goal_medal = Medals.AUTHOR
            await self._score_ui.display()

    async def set_goal_medal_gold(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.goal_medal = Medals.GOLD
            self.app.app_settings.skip_medal = max_medal(self.app.app_settings.goal_medal, self.app.app_settings.skip_medal)
            await self._score_ui.display()

    async def set_goal_medal_silver(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.goal_medal = Medals.SILVER
            self.app.app_settings.skip_medal = max_medal(self.app.app_settings.goal_medal, self.app.app_settings.skip_medal)
            await self._score_ui.display()

    async def set_skip_medal_gold(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.skip_medal = Medals.GOLD
            self.app.app_settings.goal_medal = min_medal(self.app.app_settings.skip_medal, self.app.app_settings.goal_medal)
            await self._score_ui.display()

    async def set_skip_medal_silver(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.skip_medal = Medals.SILVER
            self.app.app_settings.goal_medal = min_medal(self.app.app_settings.skip_medal, self.app.app_settings.goal_medal)
            await self._score_ui.display()

    async def set_skip_medal_bronze(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.skip_medal = Medals.BRONZE
            await self._score_ui.display()

    async def toggle_infinite_skips(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.infinite_free_skips = not self.app.app_settings.infinite_free_skips
            await self._score_ui.display()

    async def toggle_admin_fins_only(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.admin_fins_only = not self.app.app_settings.admin_fins_only
            await self._score_ui.display()

    async def toggle_allow_pausing(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings.allow_pausing = not self.app.app_settings.allow_pausing
            await self._score_ui.display()

    async def set_game_mode_rmc(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings = RMCConfig()
            await self._score_ui.display()

    async def set_game_mode_rms(self, player: Player, *args, **kwargs):
        if await self._check_player_allowed_to_change_game_settings(player):
            self.app.app_settings = RMSConfig()
            await self._score_ui.display()

    async def hide_timer(self):
        self._mode_settings[S_TIME_LIMIT] = 0
        await self._mode_manager.update_settings(self._mode_settings)

    async def _is_player_allowed_to_manage_running_game(self, player: Player) -> bool:
        if player.level == Player.LEVEL_MASTER or player == self._rmt_starter_player:
            return True
        await self._chat("You are not allowed manage running game", player)
        return False

    async def _check_player_allowed_to_change_game_settings(self, player: Player) -> bool:
        if player.level < self.app.app_settings.min_level_to_start:
            await self._chat("You are not allowed to change game settings", player)
            return False
        return True

    async def hide_custom_scoreboard(self, count, time, *args, **kwargs):
        await self._scoreboard_ui.hide()
        await self.set_original_scoreboard_visible(True)

    async def set_original_scoreboard_visible(self, visible: bool):
        self._tm_ui.properties.set_visibility(RACE_SCORES_TABLE, visible)
        self._tm_ui.properties.set_visibility(BIG_MESSAGE, visible)
        await self._tm_ui.properties.send_properties()

    async def set_time_left(self, count, time, *args, **kwargs):
        if self._game_state.is_game_stage():
            logger.info(f'ROUND_START {time} -- {count}')
            self._map_start_time = py_time.time()

    def time_left_str(self):
        tl = self._time_left
        if tl == 0:
            return "00:00:00"
        if self._game_state.is_paused:
            pause_duration = int(py_time.time() - self._time_at_pause + .5)
            tl = self._time_left_at_pause + pause_duration
        if not self._game_state.map_is_loading and not self._game_state.current_map_completed:
            tl -= int(py_time.time() - self._map_start_time + .5)
        return py_time.strftime('%H:%M:%S', py_time.gmtime(tl))
