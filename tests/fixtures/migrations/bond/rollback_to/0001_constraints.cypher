CREATE CONSTRAINT bond_user_user_id IF NOT EXISTS
FOR (u:User)
REQUIRE u.user_id IS UNIQUE;
