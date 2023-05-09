import logging
from typing import Dict, Optional
import time as py_time
from jinja2 import Template

from pyplanet.views import TemplateView
from pyplanet.views.generics.widget import TimesWidgetView

from .Data.GameScore import GameScore
from .Data.GameState import GameState
from .Data.MedalURLs import MedalURLs
from .Data.GameModes import GameModes

logger = logging.getLogger(__name__)


cb_y_off = 0.7

def cb_pos(n):
    return f".2 {-23.8 - (5.4 * n) - cb_y_off:.2f}"

def cbl_pos(n):
    return f"6.5 {-23.3 - (5.4 * n) - cb_y_off:.2f}"

def in_game_btn_pos(size_x, n_btns):
    def btn_pos(btn_ix):
        # this does basically nothing b/c of the style used for the buttons
        btn_margin = 3
        total_margins = (2 * n_btns + 1) * btn_margin
        btn_width = (size_x - total_margins) / n_btns
        btn_x_off = (btn_margin * 2 + btn_width) * btn_ix + btn_margin
        ret = f"pos=\"{btn_x_off:.2f} -3\" size=\"{btn_width:.2f} 4\""
        # logging.info(f"btn_pos returning: {ret}")
        return ret
    return btn_pos

class RandomMapsTogetherView(TimesWidgetView):
    widget_x = -100
    widget_y = 86
    z_index = 5
    size_x = 66
    size_y = 9
    title = "  Random Maps Together++  "

    template_name = "random_maps_together/widget.xml"

    async def render(self, *args, **kwargs):
        # print('render')
        ret = await super().render(*args, **kwargs)
        # print(f'render output: {ret}')
        return ret

    def __init__(self, app):
        super().__init__(self)
        logger.info("Loading VIEW")
        self.app = app
        self.manager = app.context.ui
        self.id = "it_thexivn_RandomMapsTogether_widget"
        self._score: GameScore = None
        self.ui_controls_visible = True
        self._game_state: GameState = None

    def set_score(self, score: GameScore):
        self._score = score

    def set_game_state(self, state: GameState):
        self._game_state = state

    async def get_context_data(self):
        logger.info("Context Data")
        data = await super().get_context_data()

        data["settings"] = self.app.app_settings

        if self._score:
            data["total_goal_medals"] = self._score.total_goal_medals
            data["total_skip_medals"] = self._score.total_skip_medals
        else:
            data["total_goal_medals"] = 0
            data["total_skip_medals"] = 0

        data["ui_tools_enabled"] = self.ui_controls_visible
        if self._game_state:
            data["is_paused"] = self._game_state.is_paused
            data["goal_medal_url"] = MedalURLs[self.app.app_settings.goal_medal.value].value
            data["skip_medal_url"] = MedalURLs[self.app.app_settings.skip_medal.value].value
            data["game_started"] = self._game_state.game_is_in_progress
            data["skip_medal_visible"] = self._game_state.skip_medal_available
            if self.app.app_settings.game_mode == GameModes.RANDOM_MAP_CHALLENGE:
                data["free_skip_visible"] = self._game_state.free_skip_available or self.app.app_settings.infinite_free_skips
                data["fins_count_from_name"] = self._game_state.fins_count_from_name
                data["allow_pausing"] = self.app.app_settings.allow_pausing
            if self.app.app_settings.game_mode == GameModes.RANDOM_MAP_SURVIVAL:
                data["free_skip_visible"] = True
            data["skip_pre_patch_ice_visible"] = self.app.map_handler.pre_patch_ice
            data["map_loading"] = self._game_state.map_is_loading
        else:
            data["game_started"] = False

        data['cb_pos'] = cb_pos
        data['cbl_pos'] = cbl_pos

        data['btn_pos_size'] = in_game_btn_pos(self.size_x, 2)# 3 if self.app.app_settings.allow_pausing else 2)

        return data


class RMTScoreBoard(TemplateView):
    template_name = "random_maps_together/score_board.xml"

    def __init__(self, app, score: GameScore, game_state: GameState, game):
        super().__init__(self)
        logger.info("Loading VIEW")
        self.app = app
        self.manager = app.context.ui
        self.id = "it_thexivn_RandomMapsTogether_score_board"
        self._score: GameScore = score
        self._game_state = game_state
        self._game = game

    async def get_context_data(self):
        data = await super().get_context_data()
        data["settings"] = self.app.app_settings
        data["total_goal_medals"] = self._score.total_goal_medals
        data["total_skip_medals"] = self._score.total_skip_medals
        data["goal_medal_url"] = MedalURLs[self.app.app_settings.goal_medal.value].value
        data["skip_medal_url"] = MedalURLs[self.app.app_settings.skip_medal.value].value

        data["players"] = self._score.get_top_10(20)
        data["time_left"] = self._game.time_left_str()
        time_played = self.app.app_settings.game_time_seconds - self._game._time_left + self.app.app_settings.total_time_gained
        if not self._game_state.current_map_completed and not self._game_state.map_is_loading:
            time_played += int(py_time.time() - self._game._map_start_time + .5)

        data["total_played_time"] = py_time.strftime('%H:%M:%S', py_time.gmtime(time_played))
        data["fins_count_from_name"] = self._game_state.fins_count_from_name

        data["nb_players"] = len(data['players'])
        data["scroll_max"] = max(0, len(data['players']) * 10 - 100)

        return data

    async def toggle_for(self, login):
        if login in self._is_player_shown:
            await self.hide([login])
        else:
            await self.display([login])
        logging.info(f"scoreboard toggle: {login}, {self._is_global_shown}, {self._is_player_shown}")
