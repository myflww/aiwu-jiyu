"""Explicit learned preference overrides an earlier cached automatic decision."""
import unittest
from . import test_stickers as fixture


class GrowthStickerGateTests(unittest.TestCase):
    def test_low_impact_preference_is_rechecked_before_cached_auto_gate(self):
        plugin=fixture.plugin_module.DafeiyuStickers(None,{'auto_probability':100})
        event=fixture.FakeEvent(message='今天真开心')
        event.set_extra(fixture.plugin_module.AUTO_GATE_KEY,True)
        self.assertTrue(plugin._auto_allowed(event))
        event.set_extra('dafeiyu_growth_no_auto_sticker',True)
        self.assertFalse(plugin._auto_allowed(event))
        self.assertEqual(event.get_extra(fixture.plugin_module.GATE_REASON_KEY),'scene_suppressed')

    def test_preference_does_not_erase_explicit_request_intent(self):
        plugin=fixture.plugin_module.DafeiyuStickers(None,{'auto_probability':100})
        event=fixture.FakeEvent(message='发个开心的表情包')
        event.set_extra('dafeiyu_growth_no_auto_sticker',True)
        self.assertFalse(plugin._auto_allowed(event))
        self.assertTrue(plugin._requested_sticker(event))
