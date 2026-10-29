from pathlib import Path
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import patch
from helpers import load_entry

ROOT=Path(__file__).resolve().parents[1]
effects=load_entry(ROOT,'src/effects/music_effects.py')
profiles=load_entry(ROOT,'src/effects/motion_profiles.py')


class T1AdaptationTests(unittest.TestCase):
    def test_named_resources_accept_deployed_none_and_mimic_only(self):
        for name in ('QUAD_MIMIC','BIPED_MIMIC_POPPING'):
            item=NS(tag=name,type=NS(NONE=0,MIMIC=2,value=0),intr_info=NS(intr_type=NS(value=1)))
            for value in (0,2):
                item.type.value=value
                profiles.validate_t1_resource(item,name)
            for value in (1,3,99):
                item.type.value=value
                with self.assertRaises(ValueError): profiles.validate_t1_resource(item,name)

    def test_passive_is_not_a_verified_prone_entry(self):
        controller=effects.DanceController.__new__(effects.DanceController)
        with self.assertRaises(effects.AudioError): controller.select_posture(1)

    def controller(self):
        controller=effects.DanceController.__new__(effects.DanceController)
        controller.select_posture(102)
        controller.t1_task_id=7
        controller.t1_pending=False
        controller.t1_submitted_at=9
        controller.motion_received_at=10
        controller.motion_state=NS(is_fallen=False,is_moving=False,
            form_state=NS(current_form=1),lift_state=NS(is_lifted=False),held_state=NS(is_held=False),
            task_info=NS(task_id=7))
        return controller

    def test_unknown_or_foreign_task_cannot_be_restored(self):
        with patch.object(effects.time,'monotonic',return_value=10):
            controller=self.controller()
            controller._t1_motion_guard()
            controller.motion_state.task_info.task_id=8
            with self.assertRaises(effects.AudioError): controller._t1_motion_guard(moving=True)
            controller.motion_state.task_info.task_id=7
            controller.t1_pending=True
            with self.assertRaises(effects.AudioError): controller._t1_motion_guard(moving=True)

    def test_changed_form_and_held_state_never_restore(self):
        with patch.object(effects.time,'monotonic',return_value=10):
            controller=self.controller()
            controller.motion_state.form_state.current_form=2
            with self.assertRaises(effects.AudioError): controller._t1_motion_guard(moving=True)
            controller.motion_state.form_state.current_form=1
            controller.motion_state.held_state.is_held=True
            with self.assertRaises(effects.AudioError): controller._t1_motion_guard(moving=True)
