"""LLM provider store, router and probe for the Executive.

providers.py  the ~/.hive/providers.yaml store (rows, roles, keys held apart)
templates.py  empty forms with prefilled base_url/model; nothing is created
wire.py       per-kind request bodies and response parsing
router.py     role -> ordered provider chain, failover on transport errors only
probe.py      list models, tiny prompt, plan_proposal.v1 schema round trip
api.py        /api/exec/llm/* (keys are write-only)
"""
