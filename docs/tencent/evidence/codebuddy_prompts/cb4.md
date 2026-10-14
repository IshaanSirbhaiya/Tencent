Write databridge/apps/internal_brain/tests/test_llm_chain.py covering databridge/apps/internal_brain/databridge_ib/llm.py ModelChain (read llm.py and narrate.py first):
(1) hy3 succeeds: the answer comes from model "hy3" with fallback_used False;
(2) hy3 raises: the chain falls back to "deepseek-v4.1-flash" with fallback_used True;
(3) both models fail: the chain returns no answer, so the pipeline serves the deterministic extractive answer (also assert this end to end through databridge_ib.pipeline.Pipeline using the in-memory fixtures pattern in tests/test_pipeline_core.py, expecting model "extractive-v1");
(4) a model answer containing a number that is NOT present in the context is refused by the grounding wall.
Use databridge_harness FakeBackend (see _capturing_chain in tests/test_pipeline_core.py for how a ModelChain is built over a FakeBackend); never call the network. Run the tests and fix any failure in the TEST file only (do not change llm.py or narrate.py; if you believe there is a bug in them, describe it in your summary instead).
