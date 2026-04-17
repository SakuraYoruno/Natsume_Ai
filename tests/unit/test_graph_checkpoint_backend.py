import builtins

import host_brain.integrations.graph_checkpoint as graph_checkpoint


def test_get_langgraph_checkpointer_falls_back_to_memory_when_sqlite_backend_is_missing(monkeypatch) -> None:
    original_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "langgraph.checkpoint.sqlite":
            raise ModuleNotFoundError("No module named 'langgraph.checkpoint.sqlite'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(graph_checkpoint, "_langgraph_checkpointer", None)
    monkeypatch.setattr(builtins, "__import__", _fake_import)

    checkpointer = graph_checkpoint.get_langgraph_checkpointer()

    assert type(checkpointer).__name__ == "InMemorySaver"
