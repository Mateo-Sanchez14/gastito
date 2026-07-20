-- gastito: participant nicknames (apodos) -----------------------------------

-- Extra nicknames that resolve to a Participant, so the bot recognizes e.g.
-- "Tuco"/"Tuquina" as "Fer" or "Agustin" as "Pichi". `normalized` is lowercased
-- + accent-stripped for lookup; unique per group so one apodo can't point at
-- two people.
CREATE TABLE "ParticipantAlias" (
    "id" TEXT NOT NULL,
    "participantId" TEXT NOT NULL,
    "groupId" TEXT NOT NULL,
    "alias" TEXT NOT NULL,
    "normalized" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ParticipantAlias_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "ParticipantAlias_groupId_normalized_key" ON "ParticipantAlias"("groupId", "normalized");

CREATE INDEX "ParticipantAlias_participantId_idx" ON "ParticipantAlias"("participantId");

ALTER TABLE "ParticipantAlias" ADD CONSTRAINT "ParticipantAlias_participantId_fkey"
    FOREIGN KEY ("participantId") REFERENCES "Participant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
