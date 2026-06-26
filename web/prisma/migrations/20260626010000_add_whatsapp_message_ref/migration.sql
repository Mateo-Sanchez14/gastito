-- gastito: reply-to-edit support --------------------------------------------

-- Maps a WhatsApp message id (typically the bot's confirmation message) to the
-- expense it concerns, so a member replying/quoting it can edit that expense.
CREATE TABLE "WhatsAppMessageRef" (
    "messageId" TEXT NOT NULL,
    "expenseId" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "WhatsAppMessageRef_pkey" PRIMARY KEY ("messageId")
);

CREATE INDEX "WhatsAppMessageRef_expenseId_idx" ON "WhatsAppMessageRef"("expenseId");

ALTER TABLE "WhatsAppMessageRef" ADD CONSTRAINT "WhatsAppMessageRef_expenseId_fkey"
    FOREIGN KEY ("expenseId") REFERENCES "Expense"("id") ON DELETE CASCADE ON UPDATE CASCADE;
