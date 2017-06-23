#!/usr/bin/python
# Copyright (c) 2015 Dell Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# THIS CODE IS PROVIDED ON AN *AS IS* BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED, INCLUDING WITHOUT
# LIMITATION ANY IMPLIED WARRANTIES OR CONDITIONS OF TITLE, FITNESS
# FOR A PARTICULAR PURPOSE, MERCHANTABLITY OR NON-INFRINGEMENT.
#
# See the Apache Version 2.0 License for specific language governing
# permissions and limitations under the License.

import threading

import cps
import cps_object
import cps_utils
import nas_fp_port_utils as fp_utils
import nas_front_panel_map as fp
import nas_phy_media as media
import nas_os_if_utils as nas_if
import time
import event_log as ev
import systemd.daemon

_fp_port_key = cps.key_from_name('target','base-if-phy/front-panel-port')
_media_key = cps.key_from_name('observed', 'base-pas/media')
_physical_key = cps.key_from_name('target', 'base-if-phy/physical')
_logical_if_key = cps.key_from_name('target', 'dell-base-if-cmn/if/interfaces/interface')
_logical_if_state_key = cps.key_from_name('observed', 'dell-base-if-cmn/if/interfaces-state/interface')

def _get_obj_attr_value(obj, attr_name):
    try:
        value = obj.get_attr_data(attr_name)
        return value
    except:
        return None

def set_media_transceiver(interface_obj):
    # get front panel port from ifindex
    if_obj = cps_object.CPSObject(obj=interface_obj)
    npu = if_obj.get_attr_data('base-if-phy/if/interfaces/interface/npu-id')
    port = if_obj.get_attr_data('base-if-phy/if/interfaces/interface/port-id')
    nas_if.log_info("set_media_transceiver for "+str(npu)+" , " +str(port))
    enable = if_obj.get_attr_data('if/interfaces/interface/enabled')
    port_list = nas_if.nas_os_phy_list(
        d={'npu-id': npu, 'port-id': port})
    phy_obj = cps_object.CPSObject(obj=port_list[0])
    fanout = phy_obj.get_attr_data('fanout-mode')
    try:
        hwport = phy_obj.get_attr_data('hardware-port-id')
        fp_details = fp.find_port_by_hwport(npu, hwport)
    except:
        nas_if.log_err(" Error in setting media Transceiver for ", if_obj.get_attr_data('name'))
        return
    # set media transceiver using media Id and channel ID
    # in case of 40G mode all channels should be enabled/disabled
    #  else only one channel.
    #
    nas_if.log_info("fanout " + str(fanout))
    if fanout == 2:  # then it is in 4x10G fanout mode BASE_PORT_BREAKOUT_MODE_BREAKOUT_4X1
        _lane = fp_details.lane
    else:    # non-fanout mode 1x40g mode
        _lane = None # enable/disable all channels. Do not pass Lane #
    media.media_transceiver_set(1, fp_details.media_id, _lane, enable)

def set_interface_led(interface_obj):
    if_obj = cps_object.CPSObject(obj=interface_obj)
    npu = if_obj.get_attr_data('base-if-phy/if/interfaces/interface/npu-id')
    port = if_obj.get_attr_data('base-if-phy/if/interfaces/interface/port-id')
    speed = if_obj.get_attr_data('dell-if/if/interfaces/interface/speed')
    hwport_details = nas_if.get_hwport_from_phy_port(npu, port)
    fp_port = nas_if.get_fp_from_hw_port(npu, hwport_details.hw_port)
    media_id = nas_if.get_media_id_from_fp_port(fp_port)
    media.media_led_set(1, media_id, 0, speed)


def monitor_phy_media():
    media.init_nas_media_info()
    handle = cps.event_connect()
    cps.event_register(handle, _media_key)
    while True:
        o = cps.event_wait(handle)
        obj = cps_object.CPSObject(obj=o)
        nas_if.log_info("media event received")
        try:
            # configure NPU for the media-type change event
            media.if_media_type_set(obj)
        except:
            # the event could be unrelated to media-type
            continue


class mediaMonitorThread(threading.Thread):
    def __init__(self, threadID, name):
        threading.Thread.__init__(self)
        self.threadId = threadID
        self.name = name
    def run(self):
        monitor_phy_media()
    def __str__(self):
        return ' %s %d ' %(self.name, self.threadID)

def _update_fp(fp_obj):
    fr_port = nas_if.get_cps_attr(fp_obj, 'front-panel-port')
    fp_db = fp.find_front_panel_port(fr_port)
    fp_db.set_breakout_mode(nas_if.get_cps_attr(fp_obj, 'breakout-mode'))
    fp_db.set_speed(nas_if.get_cps_attr(fp_obj, 'port-speed'))


def monitor_interface_event():
    handle = cps.event_connect()
    cps.event_register(handle, _physical_key)
    cps.event_register(handle, _logical_if_state_key)
    _led_control = media.led_control_get()
    while True:
        o = cps.event_wait(handle)
        obj = cps_object.CPSObject(obj=o)

        if _fp_port_key == obj.get_key():
            _update_fp(obj)
        if _physical_key == obj.get_key():
            continue
        elif _logical_if_state_key == obj.get_key():
            if_index = _get_obj_attr_value(obj, 'if/interfaces-state/interface/if-index')
            # check if if_index is present
            if if_index == None:
                nas_if.log_err('Interface index not present in the interface state event')
                continue
            # Get Interface attributes
            admin_state = _get_obj_attr_value(obj, 'if/interfaces-state/interface/admin-status')
            if admin_state != None:
                # This is admin state change event
                try:
                    if_obj_list = nas_if.nas_os_if_list(d={'if-index':if_index})
                    set_media_transceiver(if_obj_list[0])
                except:
                    nas_if.log_err("Unable to set media transceiver for ", str(if_index))
            if _led_control == True:
                oper_state = _get_obj_attr_value(obj, 'if/interfaces-state/interface/oper-status')
                if oper_state != None:
                    try:
                        set_interface_led(if_obj_list[0])
                    except:
                        nas_if.log_err("Error in setting LED")
                continue

class interfaceMonitorThread(threading.Thread):
    def __init__(self, threadID, name):
        threading.Thread.__init__(self)
        self.threadId = threadID
        self.name = name
    def run(self):
        monitor_interface_event()
    def __str__(self):
        return ' %s %d ' %(self.name, self.threadID)

def sigterm_hdlr(signum, frame):
    global shutdwn
    shutdwn = True

if __name__ == '__main__':

    shutdwn = False
    # Install signal handlers.
    import signal
    signal.signal(signal.SIGTERM, sigterm_hdlr)

    while cps.enabled(_media_key)  == False or cps.enabled(_fp_port_key)  == False:
        #wait for media and front panel port objects to be ready
        nas_if.log_err('Media or front panel port object is not yet ready')
        time.sleep(1)

    fp_utils.init()

    if_thread = interfaceMonitorThread(1, "Interface event Monitoring Thread")
    media_thread = mediaMonitorThread(2, "Media event Monitoring Thread")
    if_thread.daemon = True
    media_thread.daemon = True
    if_thread.start()
    media_thread.start()

    # Initialization complete
    # Notify systemd: Daemon is ready
    systemd.daemon.notify("READY=1")

    # Wait until a signal is received
    while False == shutdwn:
        signal.pause()

    systemd.daemon.notify("STOPPING=1")
    #Cleanup code here

    # No need to specifically call sys.exit(0).
    # That's the default behavior in Python.

