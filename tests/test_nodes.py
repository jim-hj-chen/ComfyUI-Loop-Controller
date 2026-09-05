"""Unit tests for queue-per-item loop semantics."""

from __future__ import annotations

import unittest
from unittest import mock

import nodes


class ListItemExtractorTests(unittest.TestCase):
    def setUp(self):
        with nodes._STATE_LOCK:  # pylint: disable=protected-access
            nodes._STATE = nodes._LoopState()  # pylint: disable=protected-access

    def test_declares_whole_list_input(self):
        self.assertIs(nodes.ListItemExtractorNode.INPUT_IS_LIST, True)
        self.assertFalse(hasattr(nodes.ListItemExtractorNode, "OUTPUT_IS_LIST"))

    def test_extracts_one_item_from_complete_list(self):
        extractor = nodes.ListItemExtractorNode()
        items = ["111", "222", "333", "444", "555"]

        self.assertEqual(extractor.extract([2], items), ("333",))

    def test_extracts_from_wrapped_list_and_multiline_text(self):
        extractor = nodes.ListItemExtractorNode()

        self.assertEqual(extractor.extract([1], [["first", "second"]]), ("second",))
        self.assertEqual(
            extractor.extract([1], ["first\n\n second \nthird"]),
            ("second",),
        )

    def test_rejects_empty_list_invalid_index_and_out_of_range(self):
        extractor = nodes.ListItemExtractorNode()

        with self.assertRaises(ValueError):
            extractor.extract([0], [])
        with self.assertRaises(ValueError):
            extractor.extract([0, 1], ["first", "second"])
        with self.assertRaises(IndexError):
            extractor.extract([2], ["first", "second"])


class QueueLoopTests(unittest.TestCase):
    def setUp(self):
        with nodes._STATE_LOCK:  # pylint: disable=protected-access
            nodes._STATE = nodes._LoopState()  # pylint: disable=protected-access

    @staticmethod
    def _prompt(start_index=0, total=5):
        return {
            "1": {
                "class_type": "Loop Start",
                "inputs": {"start_index": start_index, "total": total},
            },
            "2": {
                "class_type": "List Item Extractor",
                "inputs": {"index": ["1", 0], "list": ["source", 0]},
            },
        }

    def test_prompt_rewrite_advances_exactly_one_index_without_mutation(self):
        prompt = self._prompt(start_index=2)

        rewritten = nodes.LoopTriggerNode._inject_next_index_into_prompt(  # pylint: disable=protected-access
            prompt, next_index=3, total=5
        )

        self.assertEqual(prompt["1"]["inputs"]["start_index"], 2)
        self.assertEqual(rewritten["1"]["inputs"]["start_index"], 3)
        self.assertEqual(rewritten["1"]["inputs"]["total"], 5)

    def test_five_items_are_five_iterations_with_four_follow_up_queues(self):
        items = ["111", "222", "333", "444", "555"]
        start = nodes.LoopStartNode()
        extractor = nodes.ListItemExtractorNode()
        trigger = nodes.LoopTriggerNode()
        prompt = self._prompt()

        with mock.patch.object(nodes.LoopTriggerNode, "_queue_next") as queue_next:
            final_result = None
            session_id = ""
            for index, expected in enumerate(items):
                extra_pnginfo = None
                if index > 0:
                    extra_pnginfo = {
                        "loop_controller": {
                            "is_auto_loop": True,
                            "total": len(items),
                            "session_id": session_id,
                        }
                    }

                self.assertEqual(
                    start.run(len(items), index, extra_pnginfo),
                    (index,),
                )
                if index == 0:
                    with nodes._STATE_LOCK:  # pylint: disable=protected-access
                        session_id = nodes._STATE.session_id  # pylint: disable=protected-access

                item = extractor.extract([index], items)[0]
                self.assertEqual(item, expected)
                final_result = trigger.trigger(
                    item,
                    prompt=prompt,
                    client_id="test-client",
                    extra_pnginfo=extra_pnginfo,
                )

        self.assertEqual(queue_next.call_count, 4)
        self.assertEqual(
            [call.kwargs["next_index"] for call in queue_next.call_args_list],
            [1, 2, 3, 4],
        )
        self.assertTrue(final_result["ui"]["progress"][0]["done"])
        self.assertEqual(final_result["ui"]["progress"][0]["completed"], 5)

    def test_failed_extraction_does_not_queue_and_index_can_resume(self):
        start = nodes.LoopStartNode()
        extractor = nodes.ListItemExtractorNode()
        trigger = nodes.LoopTriggerNode()

        with mock.patch.object(nodes.LoopTriggerNode, "_queue_next") as queue_next:
            start.run(total=5, start_index=2)
            with self.assertRaises(IndexError):
                extractor.extract([2], ["111"])
            queue_next.assert_not_called()

            start.run(total=5, start_index=2)
            item = extractor.extract([2], ["111", "222", "333", "444", "555"])[0]
            trigger.trigger(item, prompt=self._prompt(start_index=2))

        queue_next.assert_called_once()
        self.assertEqual(queue_next.call_args.kwargs["next_index"], 3)


if __name__ == "__main__":
    unittest.main()
