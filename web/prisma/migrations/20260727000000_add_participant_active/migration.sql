-- gastito: "presentes" subset -----------------------------------------------

-- A trip's roster is complete from day one, but people arrive and leave at
-- different times. `active` is the second list: who a default "entre todos"
-- split actually covers. Nobody is ever deleted to achieve this (Expense.paidBy
-- cascades and would take their expenses with them), so an inactive participant
-- keeps every expense and balance they already have. Everyone starts active.
ALTER TABLE "Participant" ADD COLUMN "active" BOOLEAN NOT NULL DEFAULT true;
