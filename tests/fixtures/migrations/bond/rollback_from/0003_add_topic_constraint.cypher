CREATE CONSTRAINT bond_topic_topic_id IF NOT EXISTS
FOR (t:Topic)
REQUIRE t.topic_id IS UNIQUE;
