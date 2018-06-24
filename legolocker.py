# -*- coding: utf-8 -*-
from time import sleep

import os
import signal
import logging
import urllib
import urllib.request
import urllib.parse
import json
import pykka
from threading import Timer

from pyb00st.movehub import MoveHub
from pyb00st.constants import *

from flask import Flask, jsonify, abort, make_response

logging.basicConfig(level=logging.WARNING, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')

logger = logging.getLogger(__name__)
logger.setLevel(10)
#logger.addHandler(logging.StreamHandler())

# Slack設定
SLACK_API_URL          = os.getenv('SLACK_API_URL')
SLACK_TOKEN            = os.getenv('SLACK_TOKEN')
SLACK_BOT_NAME         = os.getenv('SLACK_BOT_NAME')
SLACK_REMINDER_CHANNEL = os.getenv('SLACK_REMINDER_CHANNEL')
REMINDER_TIMEOUT       = 5 * 60 # 5分経ったらリマインドする。

# API設定
API_SECURE_KEY = os.getenv('API_SECURE_KEY')
API_PORT       = os.getenv('API_PORT')

# 鍵やサムターンの状態
OPEN = 0
UNKNOWN = 1
CLOSED = 2

# 鍵の角度
KEY_OPEN_ANGLE = -50
KEY_EPS_ANGLE = 15

def to_status_from_color(color):
    """色に対応するサムターンの状態を返す。"""
    if color == 'BLUE':
        return CLOSED
    elif color == 'RED':
        return OPEN
    else:
        return UNKNOWN

class Messenger(object):
    """Slackにメッセージを送るクラス"""

    def send(self, channel, message):
        """Slackにメッセージを送る。"""
        try:
            req_values = {"token":SLACK_TOKEN, "channel":channel, "username":SLACK_BOT_NAME, "text":message}
            req_data = urllib.parse.urlencode(req_values).encode('ascii')
            req = urllib.request.Request(SLACK_API_URL, req_data)
            req.add_header('User-agent', 'Mozilla/5.0 (Linux i686)')
            response = urllib.request.urlopen(req, timeout=3)
            response_body = response.read()
        except Exception as e:
            logger.error('Message Sending Error', exc_info=True)

class ReminderActor(pykka.ThreadingActor):
    """鍵の開けっ放しを通知するアクター"""
    def __init__(self):
        super(ReminderActor, self).__init__()
        self._timer = None
        self._is_timed_out = False
        self._messenger = Messenger()

    def on_event(self, old_status, new_status):
        """鍵の状態変化イベントを処理する。"""
        if new_status != CLOSED and old_status == CLOSED:
            self._on_open()
        elif new_status == CLOSED and old_status != CLOSED:
            self._on_close()

    def _on_open(self):
        """鍵が開いた時のイベント。一定時間後、リマインドするタイマーを開始する。"""
        if self._timer is None:
            def notify():
                self._is_timed_out = True
                self._messenger.send(SLACK_REMINDER_CHANNEL, '鍵が開いてるよ。閉めて〜。')
            self._timer = Timer(REMINDER_TIMEOUT, notify)
            self._timer.start()

    def _on_close(self):
        """鍵が閉じた時のイベント。タイマーを止め、必要に応じて閉じたことを告げる。"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
            if self._is_timed_out:
                self._messenger.send(SLACK_REMINDER_CHANNEL, '鍵が閉まったよ。ありがと〜。')
                self._is_timed_out = False


class HubActor(pykka.ThreadingActor):
    """MoveHubをラップするアクター"""
    use_daemon_thread = True

    def __init__(self, hub):
        super(HubActor, self).__init__()
        self.hub = hub
        self._led_color = 'BLUE'

    def listen_events(self, key_angle_callback, thumb_turn_angle_callback, color_callback):
        """イベントハンドラを登録する。"""
        self.hub.listen_angle_sensor(PORT_A, key_angle_callback)
        self.hub.listen_angle_sensor(PORT_C, thumb_turn_angle_callback)
        self.hub.listen_colordist_sensor(PORT_D, color_callback)

    def get_key_angle(self):
        """鍵モーターの回転角を返す。"""
        angle = self.hub.last_angle_A
        return int(angle)

    def get_thumb_turn_motor_angle(self):
        """サムターンモーターの回転角を返す。"""
        angle = self.hub.last_angle_C
        return int(angle)

    def get_color(self):
        """サムターンの色を返す。"""
        return self.hub.last_color_D

    def key_motor_to_angle(self, angle, speed):
        """与えられた角度まで鍵モーターを回転させる。"""
        current_angle = self.get_key_angle()
        delta = angle - current_angle
        if delta != 0:
            self.hub.run_motor_for_angle(MOTOR_A, abs(delta), int(speed * delta / abs(delta)))

    def stop_key_motor(self):
        """鍵モーターを停止する。おそらく掛かる電圧がゼロになる。"""
        self.hub.run_motor_constant(MOTOR_A, 0)

    def thumb_turn_motor_to_angle(self, angle, speed):
        """与えられた角度までサムターンモーターを回転させる。"""
        current_angle = self.get_thumb_turn_motor_angle()
        delta = angle - current_angle
        if delta != 0:
            self.hub.run_motor_for_angle(MOTOR_C, abs(delta), int(speed * delta / abs(delta)))

    def stop_thumb_turn_motor(self):
        """サムターンモーターを停止する。おそらく掛かる電圧がゼロになる。"""
        self.hub.run_motor_constant(MOTOR_C, 0)

    def set_led_color(self, color):
        """LEDの色を設定する。"""
        self._led_color = color
        self.hub.set_hublight(color)

    def get_led_color(self):
        """LEDの色を返す。"""
        return self._led_color

class KeyMotorActor(pykka.ThreadingActor):
    """鍵のモーターを扱うアクター"""
    use_daemon_thread = True

    def __init__(self, hub_actor):
        super(KeyMotorActor, self).__init__()
        self.hub_actor = hub_actor

    def open(self, callback):
        """鍵を開ける方向に回転させる"""
        self.hub_actor.key_motor_to_angle(KEY_OPEN_ANGLE, 100).get()
        sleep(1)
        self.hub_actor.stop_key_motor().get()
        callback()

    def close(self, callback):
        """鍵を閉じる方向に回転させる"""
        self.hub_actor.key_motor_to_angle(0, 100).get()
        sleep(1)
        self.hub_actor.stop_key_motor().get()
        callback()

class KeyActor(pykka.ThreadingActor):
    """鍵を扱うアクター"""
    use_daemon_thread = True

    def __init__(self, hub_actor, motor_actor):
        super(KeyActor, self).__init__()
        self.hub_actor = hub_actor
        self.motor_actor = motor_actor
        self._working = False

    def is_working(self):
        """鍵モーターが稼働中かどうかを返す。"""
        return self._working

    def _set_working(self, working):
        """鍵モーターが稼働状態を設定する。"""
        logger.debug('KeyActor._set_working({})'.format(working))
        self._working = working

    def turn(self, status, callback):
        """鍵を回転させる。回転中に呼び出された場合、処理を無視する。"""
        logger.debug('KeyActor.turn({}, callback)'.format(status))
        if self.is_working():
            logger.debug("KeyActor working...")
        elif status == OPEN and self.get_status() != OPEN:
            logger.debug("open key")
            self._set_working(True)
            self.motor_actor.open(lambda: (self._set_working(False), callback()))
        elif status == CLOSED and self.get_status() != CLOSED:
            logger.debug("close key")
            self._set_working(True)
            self.motor_actor.close(lambda: (self._set_working(False), callback()))

    def get_status(self, angle=None):
        """鍵の状態を返す"""
        if angle is None:
            angle = self.hub_actor.get_key_angle().get()
        if angle < KEY_OPEN_ANGLE + KEY_EPS_ANGLE and angle > KEY_OPEN_ANGLE - KEY_EPS_ANGLE:
            return OPEN
        elif angle < KEY_EPS_ANGLE and angle > - KEY_EPS_ANGLE:
            return CLOSED
        else:
            return UNKNOWN

class ThumbTurnMotorActor(pykka.ThreadingActor):
    """サムターンのモーターを扱うアクター"""
    use_daemon_thread = True

    def __init__(self, hub_actor):
        super(ThumbTurnMotorActor, self).__init__()
        self.hub_actor = hub_actor

    def open(self, callback):
        """サムターンを開ける方向に回転させる"""
        self.hub_actor.thumb_turn_motor_to_angle(-1200, 100).get()
        sleep(1.5)
        self.hub_actor.thumb_turn_motor_to_angle(0, 100).get()
        sleep(1.5)
        self.hub_actor.stop_thumb_turn_motor().get()
        callback()

    def close(self, callback):
        """サムターンを閉じる方向に回転させる"""
        self.hub_actor.thumb_turn_motor_to_angle(1200, 100).get()
        sleep(1.5)
        self.hub_actor.thumb_turn_motor_to_angle(0, 100).get()
        sleep(1.5)
        self.hub_actor.stop_thumb_turn_motor().get()
        callback()

class ThumbTurnActor(pykka.ThreadingActor):
    """サムターンを扱うアクター"""
    use_daemon_thread = True

    def __init__(self, hub_actor, motor_actor):
        super(ThumbTurnActor, self).__init__()
        self.hub_actor = hub_actor
        self.motor_actor = motor_actor
        self._working = False

    def is_working(self):
        """サムターンモーターが稼働中かどうかを返す。"""
        return self._working

    def _set_working(self, working):
        """サムターンモーターが稼働状態を設定する。"""
        logger.debug('ThumbTurnActor._set_working({})'.format(working))
        self._working = working

    def turn(self, status, callback):
        """サムターンを回転させる。回転中に呼び出された場合、処理を無視する。"""
        logger.debug('ThumbTurnActor.turn({}, callback)'.format(status))
        if self.is_working():
            logger.debug("ThumbTurnActor working...")
        elif status == OPEN and self.get_status() == CLOSED:
            logger.debug("opening thumb turn")
            self._set_working(True)
            self.motor_actor.open(lambda: (self._set_working(False), callback()))
        elif status == CLOSED and self.get_status() == OPEN:
            logger.debug("closing thumb turn")
            self._set_working(True)
            self.motor_actor.close(lambda: (self._set_working(False), callback()))

    def get_status(self):
        """サムターンの状態を取得する"""
        color = self.hub_actor.get_color().get()
        return to_status_from_color(color)


print('turn the power on of the movehub in 10 seconds')
sleep(1)
hub = MoveHub(address=None, backend='Auto')
hub.start(timeout=1, retry=10)
hub.subscribe_all()

def sigint_handler(sig, stack):
    hub.stop()

signal.signal(signal.SIGINT, sigint_handler)
sleep(1)

hub_actor_ref = HubActor.start(hub)
hub_actor_proxy = hub_actor_ref.proxy()

key_motor_actor_ref = KeyMotorActor.start(hub_actor_proxy)
key_motor_actor_proxy = key_motor_actor_ref.proxy()

key_actor_ref = KeyActor.start(hub_actor_proxy, key_motor_actor_proxy)
key_actor_proxy = key_actor_ref.proxy()

thumb_turn_motor_actor_ref = ThumbTurnMotorActor.start(hub_actor_proxy)
thumb_turn_motor_actor_proxy = thumb_turn_motor_actor_ref.proxy()

thumb_turn_actor_ref = ThumbTurnActor.start(hub_actor_proxy, thumb_turn_motor_actor_proxy)
thumb_turn_actor_proxy = thumb_turn_actor_ref.proxy()

reminder_actor_ref = ReminderActor.start()
reminder_actor_proxy = reminder_actor_ref.proxy()

def handle_color(new_color, new_dist='', old_color='', old_dist=''):
    """色や距離が変化した時のイベントハンドラ"""
    logger.debug('handle_color({}, {}, {}, {})'.format(new_color, new_dist, old_color, old_dist))
    if not thumb_turn_actor_proxy.is_working().get():
        thumb_turn_status = thumb_turn_actor_proxy.get_status().get()
        key_actor_proxy.turn(thumb_turn_status, lambda:
            # 鍵の回転終了時に必要に応じて色イベントを発火させる。
            handle_color(hub_actor_proxy.get_color().get()) if thumb_turn_status != thumb_turn_actor_proxy.get_status().get() else None
        ).get()

    old_status = to_status_from_color(old_color)
    new_status = to_status_from_color(new_color)
    reminder_actor_proxy.on_event(old_status, new_status)

def handle_key_angle(new_angle, old_angle=0):
    """鍵モーターが回転した時のイベントハンドラ"""
    logger.debug('handle_key_angle({}, {})'.format(new_angle, old_angle))
    if not key_actor_proxy.is_working().get():
        key_status = key_actor_proxy.get_status().get()
        thumb_turn_actor_proxy.turn(key_status, lambda:
            # サムターンの回転終了時に必要に応じて鍵回転イベントを発火させる。
            handle_key_angle(hub_actor_proxy.get_key_angle().get()) if key_actor_proxy.get_status(new_angle).get() != key_actor_proxy.get_status().get() else None
        ).get()

def handle_thumb_turn_motor_angle(new_angle, old_angle):
    """サムターンモーターが回転した時のイベントハンドラ"""
    # logger.debug('thumb turn angle: {0}'.format(new_angle))
    pass

hub_actor_proxy.listen_events(
        key_angle_callback=handle_key_angle,
        thumb_turn_angle_callback=handle_thumb_turn_motor_angle,
        color_callback=handle_color
    ).get()


def to_status_string(lock_status_code):
    """開閉状態のコードに対応する文字列を返す。"""
    if lock_status_code == OPEN:
        return 'OPEN'
    elif lock_status_code == CLOSED:
        return 'CLOSED'
    else:
        return 'UNKNOWN'

def to_status_code(lock_status_string):
    """開閉状態の文字列に対応するコードを返す。"""
    if lock_status_string == 'OPEN':
        return OPEN
    elif lock_status_string == 'CLOSED':
        return CLOSED
    else:
        return UNKNOWN


api = Flask(__name__)

@api.errorhandler(500)
def error_handler(error):
    """エラーメッセージを生成するハンドラ"""
    response = jsonify({ 'cause': error.description['cause'] })
    return response, error.code

@api.route('/api/' + API_SECURE_KEY + '/key', methods=['GET'])
def get_key_status():
    """開閉状態を取得するAPI"""
    key_status = to_status_string(thumb_turn_actor_proxy.get_status().get())
    if key_status == "UNKNOWN":
        abort(500, { "cause": "unknown status." })
    result = { "key": key_status }
    return make_response(jsonify(result))

@api.route('/api/' + API_SECURE_KEY + '/key/<string:key_status_string>', methods=['PUT'])
def set_key_status(key_status_string):
    """開閉状態を変更するAPI"""
    key_status = to_status_code(key_status_string)
    if key_status != UNKNOWN and key_status != thumb_turn_actor_proxy.get_status().get():
        # サムターンの回転終了時に、鍵を開閉する。
        thumb_turn_actor_proxy.turn(key_status, lambda:
            key_actor_proxy.turn(key_status, lambda: None).get()
        ).get()
    result = { "status": "OK" }
    return make_response(jsonify(result))

@api.route('/api/' + API_SECURE_KEY + '/led/color', methods=['GET'])
def get_led_color():
    """LEDの色を返すAPI"""
    color = hub_actor_proxy.get_led_color().get()
    result = { "color": color }
    return make_response(jsonify(result))

@api.route('/api/' + API_SECURE_KEY + '/led/color/<string:color_string>', methods=['PUT'])
def set_led_color(color_string):
    """LEDの色を変更するAPI"""
    hub_actor_proxy.set_led_color(color_string).get()
    result = { "status": "OK" }
    return make_response(jsonify(result))

if __name__ == '__main__':
    api.run(host='0.0.0.0', port=API_PORT)
