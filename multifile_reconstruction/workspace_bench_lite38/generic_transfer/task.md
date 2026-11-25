# Add reusable, effective-dated department transfers

Extend this employee-management workbook so it can record and report department transfers as a reusable business function, not as a one-time correction for the example employee.

Create a `DepartmentTransfers` ledger with these first four columns: `Employee ID`, `Effective Date`, `From Department`, and `To Department`. Put the example transfer in the ledger: G01 moves from the department currently listed for that employee to Sales Dept. on 15 August 2020. The workbook must also support at least 200 ledger rows; adding a valid row must not require rewriting formulas by hand.

The department in the original employee record is the starting assignment. Keep that source value intact. For a report date, an employee belongs to the destination of their latest transfer effective on or before that date; before their first transfer, they remain in the starting department. A transfer takes effect on its stated date. Ledger records are entered in effective-date order, and a later transfer for the same employee must start from that employee's then-current department.

The existing employee lookup and current department summary must reflect the latest transfer. Monthly and annual department headcounts must reflect transfers at the report's opening and ending dates. Include an employee in a headcount only after their hire date and before their separation date. Some separated-employee records have a blank Employee ID: assign stable IDs `SEP-0001`, `SEP-0002`, … in row order starting at the first separated-employee record, so transfer history can refer to them.

A transfer is not a hire or a separation. Preserve the existing hire and separation statistics, HR-cost calculations, and contract-expiry reminders. Do not hard-code the example employee, dates, or destination into formulas. Save and return the modified workbook.
