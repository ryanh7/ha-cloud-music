import logging
from typing import Any
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import EventType
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)
from homeassistant.components.media_player import (
    ATTR_APP_ID,
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_ALBUM_ARTIST,
    ATTR_MEDIA_CHANNEL,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_DURATION,
    ATTR_MEDIA_EPISODE,
    ATTR_MEDIA_PLAYLIST,
    ATTR_MEDIA_POSITION,
    ATTR_MEDIA_POSITION_UPDATED_AT,
    ATTR_MEDIA_SEASON,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_SERIES_TITLE,
    ATTR_MEDIA_TRACK,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    ATTR_SOUND_MODE,
    ATTR_SOUND_MODE_LIST,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_CLEAR_PLAYLIST,
    SERVICE_PLAY_MEDIA,
    SERVICE_SELECT_SOUND_MODE,
    SERVICE_SELECT_SOURCE,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)

from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PLAY_PAUSE,
    SERVICE_MEDIA_SEEK,
    SERVICE_MEDIA_STOP,
    SERVICE_TOGGLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_DOWN,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
    SERVICE_VOLUME_UP,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)

from .manifest import manifest
DOMAIN = manifest.domain

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:

    entities = []
    for media_player in entry.options.get('media_player', []):
      entities.append(CloudMusicMediaPlayer(hass, media_player))

    async_add_entities(entities, True)

class CloudMusicMediaPlayer(MediaPlayerEntity):

    def __init__(self, hass, media_player):
        self.hass = hass
        self._attributes = {
            'platform': 'cloud_music'
        }
        # fixed attribute
        self._attr_media_image_remotely_accessible = True
        self._attr_device_class = MediaPlayerDeviceClass.TV.value
        self._attr_media_content_id = None

        # default attribute
        self._children = media_player
        self._attr_name = f'{manifest.name} {media_player.split(".")[1]}'
        self._attr_unique_id = f'{manifest.domain}{media_player}'
        self._attr_repeat = RepeatMode.ALL
        self._attr_shuffle = False

        self.cloud_music = hass.data['cloud_music']
        self._child_state = None
        self._music_info = None
        self._playing = False
        self._track_last_at = dt_util.now()
    
    async def async_added_to_hass(self) -> None:
        """Subscribe to children and template state changes."""

        @callback
        def _async_on_dependency_update(
            event: EventType[EventStateChangedData],
        ) -> None:
            """Update ha state when dependencies update."""
            self.async_set_context(event.context)
            self.async_schedule_update_ha_state(True)

            if (self._playing
                and self._attr_repeat != RepeatMode.OFF
                and (old_state:=event.data['old_state']) is not None
                and old_state.state == MediaPlayerState.PLAYING
                and (new_state:=event.data['new_state']) is not None
                and new_state.state == MediaPlayerState.IDLE
                and (dt_util.now() - self._track_last_at > timedelta(seconds=5))):
                self.hass.async_create_task(self.async_media_next_track())

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._children], _async_on_dependency_update
            )
        )

    def _child_attr(self, attr_name):
        """Return the active child's attributes."""
        return self._child_state.attributes.get(attr_name) if self._child_state else None

    async def _async_call_service(self, service_name, service_data=None):
        if service_data is None:
            service_data = {}

        if self._child_state is None:
            return

        service_data[ATTR_ENTITY_ID] = self._child_state.entity_id

        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN, service_name, service_data, blocking=True, context=self._context
        )

    @property
    def assumed_state(self) -> bool:
        """Return True if unable to access real state of the entity."""
        return self._child_attr(ATTR_ASSUMED_STATE)

    @property
    def state(self):
        return self._child_state.state if self._child_state else STATE_UNKNOWN
    
    @property
    def volume_level(self):
        """Volume level of entity specified in attributes or active child."""
        try:
            return float(self._child_attr(ATTR_MEDIA_VOLUME_LEVEL))
        except (TypeError, ValueError):
            return None

    @property
    def is_volume_muted(self):
        """Boolean if volume is muted."""
        return self._child_attr(ATTR_MEDIA_VOLUME_MUTED) in [True, STATE_ON]

    @property
    def media_content_type(self):
        """Return the content type of current playing media."""
        return self._child_attr(ATTR_MEDIA_CONTENT_TYPE)

    @property
    def media_duration(self):
        """Return the duration of current playing media in seconds."""
        return self._child_attr(ATTR_MEDIA_DURATION)

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        return self._music_info.thumbnail if self._music_info else None

    @property
    def entity_picture(self):
        return self.media_image_url

    @property
    def media_title(self):
        """Title of current playing media."""
        return self._music_info.song if self._music_info else None

    @property
    def media_artist(self):
        """Artist of current playing media (Music track only)."""
        return self._music_info.singer if self._music_info else None

    @property
    def media_album_name(self):
        """Album name of current playing media (Music track only)."""
        return self._music_info.album if self._music_info else None

    @property
    def media_album_artist(self):
        """Album artist of current playing media (Music track only)."""
        return self._child_attr(ATTR_MEDIA_ALBUM_ARTIST)

    @property
    def media_track(self):
        """Track number of current playing media (Music track only)."""
        return self._child_attr(ATTR_MEDIA_TRACK)

    @property
    def media_series_title(self):
        """Return the title of the series of current playing media (TV)."""
        return self._child_attr(ATTR_MEDIA_SERIES_TITLE)

    @property
    def media_season(self):
        """Season of current playing media (TV Show only)."""
        return self._child_attr(ATTR_MEDIA_SEASON)

    @property
    def media_episode(self):
        """Episode of current playing media (TV Show only)."""
        return self._child_attr(ATTR_MEDIA_EPISODE)

    @property
    def media_channel(self):
        """Channel currently playing."""
        return self._child_attr(ATTR_MEDIA_CHANNEL)

    @property
    def media_playlist(self):
        """Title of Playlist currently playing."""
        return self._child_attr(ATTR_MEDIA_PLAYLIST)

    @property
    def app_id(self):
        """ID of the current running app."""
        return self._child_attr(ATTR_APP_ID)

    @property
    def app_name(self):
        """Name of the current running app."""
        return self._music_info.singer if self._music_info else None

    @property
    def sound_mode(self):
        """Return the current sound mode of the device."""
        return self._child_attr(ATTR_SOUND_MODE)

    @property
    def sound_mode_list(self):
        """List of available sound modes."""
        return self._child_attr(ATTR_SOUND_MODE_LIST)

    @property
    def source(self):
        """Return the current input source of the device."""
        return self._child_attr(ATTR_INPUT_SOURCE)

    @property
    def source_list(self):
        """List of available input sources."""
        return self._child_attr(ATTR_INPUT_SOURCE_LIST)

    @property
    def media_position(self):
        """Position of current playing media in seconds."""
        return self._child_attr(ATTR_MEDIA_POSITION)

    @property
    def media_position_updated_at(self):
        """When was the position of the current playing media valid."""
        return self._child_attr(ATTR_MEDIA_POSITION_UPDATED_AT)

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        flags: MediaPlayerEntityFeature = self._child_attr(
            ATTR_SUPPORTED_FEATURES
        ) or MediaPlayerEntityFeature(0)

        flags |= MediaPlayerEntityFeature.BROWSE_MEDIA | \
                MediaPlayerEntityFeature.PREVIOUS_TRACK | \
                MediaPlayerEntityFeature.NEXT_TRACK | \
                MediaPlayerEntityFeature.PLAY_MEDIA | \
                MediaPlayerEntityFeature.SHUFFLE_SET | \
                MediaPlayerEntityFeature.REPEAT_SET

        return flags

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        await self._async_call_service(SERVICE_TURN_ON)

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        self._playing = False
        await self._async_call_service(SERVICE_TURN_OFF)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute the volume."""
        data = {ATTR_MEDIA_VOLUME_MUTED: mute}
        await self._async_call_service(SERVICE_VOLUME_MUTE, data)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        data = {ATTR_MEDIA_VOLUME_LEVEL: volume}
        await self._async_call_service(SERVICE_VOLUME_SET, data)

    async def async_media_play(self) -> None:
        """Send play command."""
        self._playing = True
        self._track_last_at = dt_util.now()
        await self._async_call_service(SERVICE_MEDIA_PLAY)

    async def async_media_pause(self) -> None:
        """Send pause command."""
        self._playing = False
        await self._async_call_service(SERVICE_MEDIA_PAUSE)

    async def async_media_stop(self) -> None:
        """Send stop command."""
        self._playing = False
        await self._async_call_service(SERVICE_MEDIA_STOP)

    async def async_media_previous_track(self) -> None:
        """Send previous track command."""
        self._track_last_at = dt_util.utcnow()
        await self.cloud_music.async_media_previous_track(self, self._attr_shuffle)

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        self._track_last_at = dt_util.utcnow()
        await self.cloud_music.async_media_next_track(self, self._attr_shuffle)

    async def async_media_seek(self, position: float) -> None:
        """Send seek command."""
        data = {ATTR_MEDIA_SEEK_POSITION: position}
        await self._async_call_service(SERVICE_MEDIA_SEEK, data)

    async def async_volume_up(self) -> None:
        """Turn volume up for media player."""
        await self._async_call_service(SERVICE_VOLUME_UP)

    async def async_volume_down(self) -> None:
        """Turn volume down for media player."""
        await self._async_call_service(SERVICE_VOLUME_DOWN)

    async def async_media_play_pause(self) -> None:
        """Play or pause the media player."""
        await self._async_call_service(SERVICE_MEDIA_PLAY_PAUSE)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        """Select sound mode."""
        data = {ATTR_SOUND_MODE: sound_mode}
        await self._async_call_service(SERVICE_SELECT_SOUND_MODE, data)

    async def async_select_source(self, source: str) -> None:
        """Set the input source."""
        data = {ATTR_INPUT_SOURCE: source}
        await self._async_call_service(SERVICE_SELECT_SOURCE, data)

    async def async_clear_playlist(self) -> None:
        """Clear players playlist."""
        await self._async_call_service(SERVICE_CLEAR_PLAYLIST)

    async def async_toggle(self) -> None:
        """Toggle the power on the media player."""
        await self._async_call_service(SERVICE_TOGGLE)
    
    async def async_update(self) -> None:
        """Update state in HA."""
        self._child_state = self.hass.states.get(self._children)
        self._music_info = self.playlist[self.playindex] if hasattr(self, 'playlist') else None
    
    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Enable/disable shuffling."""
        self._attr_shuffle = shuffle

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set repeat mode."""
        self._attr_repeat = repeat

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a piece of media."""
        media_content_id = media_id
        result = await self.cloud_music.async_play_media(self, self.cloud_music, media_id)
        if result is not None:
            if result == 'index':
                # 播放当前列表指定项
                media_content_id = self.playlist[self.playindex].url
            elif result.startswith('http'):
                # HTTP播放链接
                media_content_id = result
            else:
                # 添加播放列表到播放器
                media_content_id = self.playlist[self.playindex].url

        self._attr_media_content_id = media_content_id
        data = {ATTR_MEDIA_CONTENT_TYPE: MediaType.MUSIC, ATTR_MEDIA_CONTENT_ID: media_content_id}
        await self._async_call_service(SERVICE_PLAY_MEDIA, data)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        return await self.cloud_music.async_browse_media(self, media_content_type, media_content_id)

    @property
    def device_info(self):
        return {
            'identifiers': {
                (DOMAIN, manifest.documentation)
            },
            'name': self.name,
            'manufacturer': 'NetEase',
            'model': 'CloudMusic',
            'sw_version': manifest.version
        }

