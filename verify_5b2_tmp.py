import ast
import pathlib


def defs(p):
    return sorted(
        n.name
        for n in ast.walk(ast.parse(pathlib.Path(p).read_text(encoding="utf-8")))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


expected = {
    "session": [
        "_build_character_round_text",
        "_character_is_isolated",
        "_character_to_snapshot",
        "_create_message_with_shadow",
        "_detect_communication_channel",
        "_directly_addressed_ids",
        "_effective_prior_replies",
        "_is_location_allowed",
        "_load_location_descriptions",
        "_log_generation_diagnostics",
        "_message_snapshot",
        "_message_to_dict",
        "_parse_allowed_locations",
        "_parse_known_locations",
        "_scene_gate_confirms",
        "process_user_message",
    ],
    "story": [
        "_belief_evidenced_ids",
        "_chat_plot_text",
        "_chat_story_block",
        "_compute_epistemic_evidence",
    ],
    "lora": [
        "_default_lora_manager",
        "lora_first_apply_warning",
        "resolve_generation_model",
    ],
}
for m, v in expected.items():
    got = defs("app/pipeline/" + m + ".py")
    print(m, "OK" if got == v else "FAIL: %s" % got)

print("streaming:", [n for n in defs("app/pipeline/streaming.py")])
print("regeneration:", defs("app/pipeline/regeneration.py"))
print("facade:", defs("app/chat_engine.py"))

import app.chat_engine as ce
from app.pipeline import session, story, lora, streaming, regeneration

checks = {
    "streaming identity": ce.process_user_message_streaming is streaming.process_user_message_streaming,
    "regeneration identity": ce.regenerate_message_streaming is regeneration.regenerate_message_streaming,
    "process_user_message identity": ce.process_user_message is session.process_user_message,
    "snapshot identity": ce._message_snapshot is session._message_snapshot,
    "story identity": ce._chat_story_block is story._chat_story_block,
    "epistemic identity": ce._compute_epistemic_evidence is story._compute_epistemic_evidence,
    "belief identity": ce._belief_evidenced_ids is story._belief_evidenced_ids,
    "lora identity": ce.resolve_generation_model is lora.resolve_generation_model,
    "warned identity": ce._lora_unknown_warned_chats is lora._lora_unknown_warned_chats,
}
for k, v in checks.items():
    print(("OK  " if v else "FAIL"), k)

story_t = open("app/pipeline/story.py", encoding="utf-8").read()
stream_t = open("app/pipeline/streaming.py", encoding="utf-8").read()
print("story lazy facade import:", "from ..chat_engine import _build_pair_relationship_context, _evidence_mode" in story_t)
print("streaming lazy facade import:", "from ..chat_engine import _analyze_and_update_relationships" in stream_t)
print("streaming lazy debug import:", "from ..routers.debug import remember_pipeline_report" in stream_t)
