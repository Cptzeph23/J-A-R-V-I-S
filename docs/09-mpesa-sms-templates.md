# 09 - M-PESA SMS templates

Date: 2026-10-29 | Source: eight screenshots supplied by the owner on 2026-10-27, with names, codes and
account numbers redacted here and in the test fixtures (`apps/phonelog/tests/test_mpesa_parser.py`).

The parser accepts only the layouts below. Anything else from an allowlisted sender is stored as
`unparsed` (counted, text discarded) and never guessed at. Whitespace and line wrapping carry no
meaning (the screenshots show wrapped lines and doubled spaces inside names).

## Supported (observed in real messages)

| Template | Layout (redacted) | Becomes |
|---|---|---|
| `mpesa_send` | `<CODE> Confirmed. Ksh20.00 sent to <NAME> on 26/10/26 at 6:19 PM. New M-PESA balance is Ksh0.00. Transaction cost, Ksh0.00. Amount you can transact within the day is 499,800.00. See all your balances now https://saf.cx/...` | expense, merchant = NAME |
| `mpesa_paybill` | `<CODE> Confirmed. Ksh50.00 sent to <BANK NAME>. for account <ACCOUNT> on 27/10/26 at 7:19 AM New M-PESA balance ...` (note: no period after the time) | expense, merchant = BANK NAME, account reference kept in the description |
| `mpesa_paid` | `<CODE> Confirmed. Ksh20.00 paid to <NAME>. on 26/10/26 at 7:06 PM.New M-PESA balance ...` (note: no space after the period) | expense, merchant = NAME |
| `fuliza` | `<CODE> Confirmed. Fuliza M-PESA amount is Ksh 50.00. Access Fee charged Ksh 0.50. Total Fuliza M-PESA outstanding amount is Ksh1266.51 due on 21/11/26. To check daily charges, Dial *334#OK Select Query Charges` | fee transaction for the access fee only |

## Accounting rules

- Dates are `d/m/yy`, times are 12-hour Kenyan wall-clock time (`Africa/Nairobi`), converted to UTC.
- A non-zero `Transaction cost` becomes a second transaction of kind `fee` with reference `<CODE>:fee`.
- A Fuliza notice shares its code with the payment it funded (observed: `AB1CD2EF3L` appears on both). The draw
  is financing, not spending, because the payment message already recorded the outflow; only the access fee is
  an expense (`<CODE>:fuliza_fee`). The outstanding balance and due date are parsed but not yet stored.
- Every SMS-sourced transaction is `unverified` until a statement import contains the same M-PESA code
  (threat T14). Reports flag unverified rows.
- Allowlisted senders default to `MPESA` (case-insensitive, exact match). Set `PHONE_FINANCIAL_SENDERS` to change.
  The screenshots did not show the sender id, so please confirm it.

## Not supported yet (needs real examples)

Please send redacted examples of any of these you receive, one screenshot each:

1. Money received (`You have received Ksh... from ...`)
2. Withdrawal at an agent or ATM
3. Airtime purchase and data bundles
4. Fuliza repayment (`... from your M-PESA has been used to ... pay your outstanding Fuliza M-PESA`)
5. Deposits, reversals, Pochi la Biashara, Lipa na M-PESA till payments with a different wording
6. M-Shwari or KCB M-PESA sweeps, if you use them

Until then these arrive as `unparsed`. Because message text is not stored, a message that was `unparsed`
stays `unparsed` after the parser learns its layout; the collector must re-upload it under a new
`source_record_id` (a full re-sync from the app does this).
