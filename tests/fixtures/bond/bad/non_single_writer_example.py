from host_brain.bond.neo4j_client import InMemoryNeo4jBondClient


def bad_write() -> None:
    client = InMemoryNeo4jBondClient()
    client.write_bond_event(
        writer_id="host_brain.other.module",
        event_id="evt_bad_001",
        user_id="user_001",
        session_id="session_001",
        topic_id="topic_001",
        capability_hint="SUPPORTIVE",
        evidence_refs=["evidence::bad"],
        payload={"etype": "TEST"},
        ts_ms=1,
    )
