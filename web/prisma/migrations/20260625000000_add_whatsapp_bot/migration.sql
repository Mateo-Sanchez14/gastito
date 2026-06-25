-- gastito: WhatsApp bot integration --------------------------------------

-- Expense provenance + idempotency
ALTER TABLE "Expense" ADD COLUMN     "source" TEXT,
                      ADD COLUMN     "externalId" TEXT;

-- Dedupe Gowa webhook retries (Postgres treats NULLs as distinct, so manual
-- expenses with NULL source/externalId are never blocked by this constraint).
CREATE UNIQUE INDEX "Expense_source_externalId_key" ON "Expense"("source", "externalId");

-- One WhatsApp group <-> one spliit Group
CREATE TABLE "WhatsAppGroupLink" (
    "id" TEXT NOT NULL,
    "chatId" TEXT NOT NULL,
    "groupId" TEXT NOT NULL,
    "fxArsSource" TEXT NOT NULL DEFAULT 'blue',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "WhatsAppGroupLink_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "WhatsAppGroupLink_chatId_key" ON "WhatsAppGroupLink"("chatId");

ALTER TABLE "WhatsAppGroupLink" ADD CONSTRAINT "WhatsAppGroupLink_groupId_fkey"
    FOREIGN KEY ("groupId") REFERENCES "Group"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- Sender within a group -> spliit Participant
CREATE TABLE "WhatsAppMember" (
    "id" TEXT NOT NULL,
    "chatId" TEXT NOT NULL,
    "senderJid" TEXT NOT NULL,
    "participantId" TEXT NOT NULL,
    "displayName" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "WhatsAppMember_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "WhatsAppMember_chatId_senderJid_key" ON "WhatsAppMember"("chatId", "senderJid");

ALTER TABLE "WhatsAppMember" ADD CONSTRAINT "WhatsAppMember_participantId_fkey"
    FOREIGN KEY ("participantId") REFERENCES "Participant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
