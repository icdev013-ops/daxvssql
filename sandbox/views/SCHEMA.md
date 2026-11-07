# Route B schema: mirrored Power BI tables

Each franchise has its own MySQL schema (`poc_icla`, `poc_icch`, `poc_icau`) holding one view per table of that franchise's Power BI model, with the same table names, column names, column order and rows. The views are defined in `10_mirror.sql` plus the franchise-only files. The franchise is fixed by the schema: every row of every view belongs to the connected franchise, so no entity filter is needed (the only exceptions are the shared lookups `DateTable`, `SoldDateTable`, `Lead Source Channels`, `Source_Sort`, `LS Group Sort` and the franchise list `dim_entity`). There are **no measures**: every metric has to be written as SQL. Column notes repeat the model's descriptions (without its Q&A synonym lists), followed by facts checked against the data. Gaps against the model are listed in `PARITY_NOTES.md`.

**Conventions**
- Quote every name that has a space, punctuation or a reserved word with backticks: `` `Revised Stage` ``, `` `Lead Milestones` ``, `` `Index` ``, `` `Year-Month` ``, `` `Converted Project: Project Name` ``, `` `Inspired Closets Lead Source.1` ``. Without ANSI_QUOTES, `"Revised Stage"` is a string literal, so `"Revised Stage" = 'Sold'` silently matches nothing.
- View names are case-insensitive (the server stores them in lower case); column names are as listed.
- DATE columns hold dates only. The DATETIME date columns of `Salesforce_Data` and `Leads`.`Lead Conversion Date`, `Opportunities to Appointments`.`Converted Date` and `Leads_Journey`.`First Status Date` hold midnight values. `Leads`.`Lead Creation Timestamp`, `Leads_Journey`.`Latest Status Date` / `Last Updated` / `Latest File Date` and the audit columns (`Data Ingested At`, `created_at`, `source_created_at`, `data_ingested_at`) carry a time of day. Filter any DATETIME column with a half-open range (`>= '2026-01-01' AND < '2026-02-01'`), never `<= '2026-01-31'`.
- NULL is Power BI's blank. Text comparisons are case-insensitive, as in Power BI.
- Rows have no inherent order: always `ORDER BY` (see each column's "Sort by" note for the model's display order).

| Views | Shared by ICLA, ICCH, ICAU |
|---|---|
| Facts | `Salesforce_Data`, `Leads`, `Appointments`, `Leads_Journey`, `Opportunities to Appointments`, `Lead Milestones` |
| Dimensions and lookups | `dim_projects`, `dim_designers`, `DateTable`, `SoldDateTable`, `Lead Source Channels`, `Source_Sort`, `LS Group Sort`, `dim_entity` |
| Weekly-report snapshots (no relationships) | `report_scorecard`, `report_designer_leaderboard`, `report_lead_source_roi`, `report_designer_concentration`, `report_channel_roi`, `report_appointment_funnel` |
| Franchise-only | `Projects Part List` (ICLA); column `Salesforce_Data`.`Additional Lead Source` (ICAU) |

## Relationships as join keys

These are the model's relationships (`relationships.tmdl`). MySQL has no foreign keys: write the join yourself. In Power BI a row whose key has no match on the "one" side still counts, under a blank member; in SQL use a LEFT JOIN to keep it. "Active" is the path Power BI filters through by default; an inactive relationship is only used when a measure asks for it. "Both" means filters flow both ways in Power BI; SQL has no such notion.

| From (many side) | To (one side) | Cardinality | Active | Filter direction | Scope |
|---|---|---|---|---|---|
| Salesforce_Data.`Created Date` | DateTable.`Date` | many-to-one | active | single | all |
| Salesforce_Data.`Original Project Sold Date` | SoldDateTable.`Date` | many-to-one | active | single | all |
| Salesforce_Data.`Original Project Sold Date` | DateTable.`Date` | many-to-one | **inactive** | single | all |
| Salesforce_Data.`Project Name` | dim_projects.`Project Name` | many-to-one | active | single | all |
| Salesforce_Data.`Designer` | dim_designers.`Designer` | many-to-one | active | single | all |
| Salesforce_Data.`Lead Source` | Source_Sort.`Lead Source` | many-to-one | active | single | all |
| Salesforce_Data.`Lead Source Group` | `LS Group Sort`.`Lead Source Group` | many-to-one | active | single | all |
| Salesforce_Data.`entity` | dim_entity.`entity` | many-to-one | active | single | all |
| Leads.`Lead Created Date` | DateTable.`Date` | many-to-one | active | single | all |
| Leads.`Converted Project Name` | dim_projects.`Project Name` | many-to-one | active | single | all |
| Leads.`Lead Source Channel` | `Lead Source Channels`.`Inspired Closets Lead Source` | many-to-one | active | single | all |
| Leads.`Lead ID` | Leads_Journey.`Lead ID` | many-to-one (1:1 in data) | active | single | all |
| Leads.`entity` | dim_entity.`entity` | many-to-one | active | single | all |
| `Lead Milestones`.`Lead ID` | Leads.`Lead ID` | many-to-one (1:1 in data) | active | **both** | all |
| Appointments.`Appointment Start Time` | DateTable.`Date` | many-to-one | active | single | all |
| Appointments.`Project Name` | dim_projects.`Project Name` | many-to-one | active | single | all |
| Appointments.`designer_sk` | dim_designers.`designer_sk` | many-to-one | active | single | all |
| Appointments.`Lead Source Channel` | `Lead Source Channels`.`Inspired Closets Lead Source` | many-to-one | active | single | all |
| Appointments.`entity` | dim_entity.`entity` | many-to-one | active | single | all |
| `Opportunities to Appointments`.`Converted Project: Project Name` | dim_projects.`Project Name` | many-to-one | active | **both** | all |
| `Opportunities to Appointments`.`Activity Date and Time` | DateTable.`Date` | many-to-one | **inactive** | single | all |
| Source_Sort.`Lead Source` | `Lead Source Channels`.`Inspired Closets Lead Source` | **one-to-one** | active | **both** | all |
| Leads_Journey.`entity_sk` | dim_entity.`entity_sk` | many-to-one | **inactive** | single | all |
| `Projects Part List`.`project_name` | dim_projects.`Project Name` | many-to-one | active | single | ICLA only |
| `Projects Part List`.`entity` | dim_entity.`entity` | many-to-one | active | single | ICLA only |

Join form: `FROM Salesforce_Data AS s LEFT JOIN dim_designers AS d ON d.Designer = s.Designer`. `Salesforce_Data` and `Appointments` have **no** relationship to each other; both point at `dim_projects`, and Power BI filters do not flow from one to the other through it.

## Traps

1. **Appointments to sales is a name match that fans out.** The only link is `Project Name`, which repeats in `Salesforce_Data` (60 / 60 / 52 duplicate-name rows in ICLA / ICCH / ICAU) and across many appointments, and is NULL for about a quarter to a third of appointments (tasks, internal and availability events). Joining the two views row to row double-counts: aggregate each side per `Project Name` first, or use `EXISTS`.
2. **DateTable only covers 2020-01-01 to 2030-12-31.** Opportunities created before 2020 (1,535 ICLA, 1,175 ICCH, 1,025 ICAU) fall outside it. Power BI's "all time" through DateTable leaves them out; an unbounded SQL query includes them.
3. **Two date roles on `Salesforce_Data`.** `Created Date` relates to `DateTable` (active); `Original Project Sold Date` relates to `SoldDateTable` (active) and to `DateTable` (inactive). In SQL pick the column explicitly. `Original Project Sold Date` is NULL for unsold rows and for many older Sold rows (2,865 ICLA, 3,051 ICCH, 2,030 ICAU), and some non-Sold rows still carry one (45 / 33 / 37 sold-then-cancelled rows), so sold-date questions need `Revised Stage` = 'Sold' as well.
4. **`dim_designers` holds active designers only** (14 ICLA, 31 ICCH, 30 ICAU). `Salesforce_Data` rows whose `Designer` is not active (4,488 / 2,872 / 1,380) and appointments whose `designer_sk` is not active (7,212 / 1,181 / 2,660) drop out of an inner join; Power BI shows them under a blank designer. Grouping by `Salesforce_Data`.`Designer` instead lists inactive and x-prefixed former designers by name.
5. **Date-cast columns.** `Appointments`.`Activity Date and Time`, `Appointment Start Time` and `Start Of WeekA`, `Leads`.`Lead Created Date` and `Opportunities to Appointments`.`Activity Date and Time` and `Created Date` are DATE: the model drops the time of day, and so do the views.
6. **Week starts are Sundays** (`Start of Week`, `Start Of WeekA`, `Sold Start of Week`, `DateTable`.`Start Of Week`), but `DateTable`.`WeekdayNumber` counts Monday = 1 to Sunday = 7. `Start Of WeekA` follows `Activity Date and Time`, not `Appointment Start Time`.
7. **Flags have mixed types.** `Is Real Appointment` and `Is Future Appointment` are the text values 'TRUE' / 'FALSE'; `Is First Appointment`, `Is Converted Lead`, `Is Referral Lead`, `Has Showroom Visit`, `is_likely_duplicate` and the `Lead Milestones` 0/1 columns are integers; `hygiene_flag` and `warn_flag` are 0/1.
8. **`Appointments` mixes events and tasks.** `Activity Type` = 'Unspecified' rows are CRM tasks (6,249 ICLA, 15,069 ICCH, 14,296 ICAU). `Appointments`.`Project Stage` never contains 'Complete'.
9. **Frozen Gold values.** `Is Future Appointment`, `Lead Age in Days`, `Lead Age Category` and `dim_designers`.`active_flag` were computed when Gold was last built, not at query time.
10. **`Revised Stage` has exactly four values:** 'Sold', 'Proposed', 'Lost', 'Dead Lead'. Dead Lead is separate from Lost.
11. **`report_*` views keep every weekly snapshot** (about 150 `report_date` values). For current figures filter `report_date = (SELECT MAX(report_date) FROM <view>)`. They relate to nothing.
12. **`dim_entity` lists every franchise.** It is the model's franchise list, not data; all fact rows belong to this franchise only.
13. **`Leads_Journey` has a few leads missing** (7 ICLA, 6 ICCH, 3 ICAU leads have no journey row); `Lead Milestones` has exactly one row per lead.
14. **Blank versus NULL.** Power BI treats blank as equal to '' and 0; in SQL `= ''` does not match NULL, so use `IS NULL` or `COALESCE`.

## Views

### `Salesforce_Data`

One row per opportunity (Salesforce project): stage, amount, created and sold dates, designer, lead source. `Project ID` is unique; `Project Name` is NOT. Source: `icall_gold.fact_opportunities` (= fact_sales), 24 selected columns (ICAU 25) + 4 calculated columns.

| Column | Type | Notes |
|---|---|---|
| `Project Name` | varchar(255) |  |
| `Stage` | varchar(100) | Raw Salesforce stage: Complete, Ready to Install, Ready to Order, Incomplete, Design, Propose, Lost, Dead Lead. |
| `Original Project Sold Date` | datetime | Sold date, midnight datetime. NULL when never sold (and for many older Sold rows). |
| `Amount` | decimal(18,2) | Project value in dollars (all stages). |
| `Created Date` | datetime | Opportunity created date, midnight datetime. |
| `Last Modified Date` | datetime |  |
| `Lead Source` | varchar(100) |  |
| `Additional Lead Source` | varchar(255) | Additional/Other lead source free-text detail (maps to gold column "Other Lead Source", SF Other_Lead_Source__c). Shown mainly when Lead Source = "Other". ICAU only. |
| `Project Notes` | text |  |
| `Project ID` | varchar(50) |  |
| `Consumer Name: Full Name` | varchar(255) |  |
| `Start of Week` | datetime | Sunday on or before `Created Date`. |
| `Created Month` | varchar(10) | 'Jan'..'Dec' of `Created Date` (no year). |
| `Sold Year` | int | Calculated column: YEAR(sold date), NULL when no sold date. |
| `Sold Month Number` | int | Calculated column: MONTH(sold date), NULL when no sold date. (hidden in the model) |
| `Sold Month` | varchar(3) | Calculated column: 'Jan'..'Dec' of the sold date, NULL when no sold date. Sort by `Sold Month Number`. |
| `Sold Start of Week` | datetime | Calculated column: Sunday on or before the sold date, NULL when no sold date. |
| `Lead Source Group` | varchar(50) |  |
| `Designer` | varchar(255) | Designer name as text (includes inactive and x-prefixed former designers). |
| `Revised Stage` | varchar(100) | Exactly 4 values: 'Sold' (Stage Complete / Ready to Install / Ready to Order / Incomplete), 'Proposed' (Design / Propose), 'Lost', 'Dead Lead'. |
| `Sale Cycle` | bigint | Days from `Created Date` to sold date. |
| `Sale to Complete Days` | bigint | Days from sold date to `Final Completion Date`. |
| `Lead Time` | bigint | Days from sold date to the target install date. |
| `opportunity_sk` | bigint |  |
| `entity` | varchar(10) |  |
| `Data Ingested At` | datetime |  |
| `Final Completion Date` | datetime |  |
| `Target Ready to Order Date` | datetime | Empty for ICAU. |
| `Owner First Appointments Last 12 Months` | int | Empty for ICAU. |

### `Leads`

One row per lead (`Lead ID` unique): source channel, status, contact details, conversion to a project. Source: `icall_gold.fact_leads`, all columns, `Lead Created Date` cast to date.

| Column | Type | Notes |
|---|---|---|
| `Index` | bigint | Reserved word: always write `Index`. |
| `Lead Created Date` | date | Date when the lead was first created in the system. DATE (the model casts it to date). |
| `Lead Source Channel` | varchar(100) | Marketing channel or source where the lead originated. |
| `Lead Status` | varchar(100) | Current status of the lead in the sales process. |
| `Lead Capture Form Type` | varchar(100) | Type of form used to capture the lead. |
| `Lead Creator Name` | varchar(255) | Full name of the person who created the lead record. |
| `Customer First Name` | varchar(100) | Customer's first name. |
| `Customer Last Name` | varchar(100) | Customer's last name. |
| `Customer Email` | varchar(255) | Customer's email address. |
| `Customer Phone` | varchar(50) | Customer's phone number. |
| `Showroom Location` | varchar(100) | Showroom location associated with the lead. |
| `Customer Street Address` | varchar(255) | Street address of the customer. |
| `Customer City` | varchar(100) | City where the customer is located. |
| `Customer State` | varchar(50) | State or province of the customer. |
| `Customer ZIP Code` | varchar(20) | Postal or ZIP code of the customer. |
| `Preferred Call Time` | varchar(50) | Preferred time for contact calls. |
| `Preferred Call Day` | varchar(50) | Preferred day for contact calls. |
| `Contact Time Preference` | varchar(100) | Customer's general time preference for contact. |
| `Is Referral Lead` | bigint | Indicator if this lead came from another lead. 1/0. |
| `Consultation Requested` | varchar(50) | Whether customer requested a consultation. |
| `Is Converted Lead` | bigint | Indicator if the lead has been converted to a customer. 1/0. |
| `Customer Account Number` | varchar(200) | Account number assigned when lead converts. |
| `Converted Customer Name` | varchar(255) | Full name of the converted contact. |
| `Lead Conversion Date` | datetime | Date when the lead was converted to a customer. |
| `Converted Project Name` | varchar(255) | Name of the project created from the converted lead. Joins `dim_projects`.`Project Name` / `Salesforce_Data`.`Project Name`. |
| `Interested Home Area` | varchar(255) | Area of the home customer is interested in. |
| `Lead Source from URL` | varchar(255) | Lead source information captured from URL parameters. |
| `Lead ID` | varchar(50) | Unique identifier for the lead. |
| `Lead Creation Timestamp` | datetime | Exact date and time when lead was created. |
| `Lead Type` | varchar(100) | Classification type of the lead. |
| `Lead Quality Rating` | varchar(50) | Quality rating or score of the lead. |
| `Lead Description` | text | Detailed description or notes about the lead. |
| `Initial Comments` | text | Comments captured during lead creation. |
| `Project Notes` | text | Project-related notes for the lead. |
| `Lead Disqualification Reason` | varchar(255) | Reason why lead was marked as junk. |
| `Lead Nurturing Reason` | varchar(255) | Reason why lead is in nurturing status. |
| `Has Showroom Visit` | bigint | Indicator if customer visited showroom. 1/0. |
| `Additional Source Information` | varchar(255) | Additional details about the lead source. |
| `Preferred Communication Method` | varchar(50) | Customer's preferred method for communication. |
| `Estimated Home Value` | varchar(50) | Estimated value of the customer's home. |
| `Lead Status Category` | varchar(50) | High-level category classification of the lead. |
| `Lead Age in Days` | bigint | Number of days since lead was created. Computed when Gold was last built, not live. |
| `Lead Age Category` | varchar(20) | Age category bucket for the lead. |
| `Lead Creation Year-Month` | varchar(10) | Year and month when lead was created. 'YYYY-MM'. |
| `Customer Full Name` | varchar(255) |  |
| `Contact Attempt Stage` | bigint | Number indicating response attempt stage (now physical in fact_leads, computed in Gold). |
| `DataType` | varchar(20) |  |
| `Customer Full Address` | varchar(500) |  |
| `lead_sk` | bigint |  |
| `entity` | varchar(10) |  |
| `is_likely_duplicate` | tinyint | 1/0 Gold duplicate flag. |
| `duplicate_of_lead_id` | varchar(50) |  |
| `Data Ingested At` | datetime |  |
| `Customer State Code` | char(2) |  |
| `customer_sk` | bigint |  |
| `geography_sk` | bigint |  |
| `lead_source_sk` | bigint |  |
| `lead_status_sk` | bigint |  |
| `form_type_sk` | bigint |  |
| `project_sk` | bigint |  |
| `entity_sk` | bigint |  |

### `Appointments`

One row per calendar event or CRM task (`appointment_key` unique). Includes tasks (`Activity Type` = 'Unspecified') and internal or availability blocks. Source: `icall_gold.fact_appointments`, all columns, 3 date casts.

| Column | Type | Notes |
|---|---|---|
| `Activity Date and Time` | date | Date and time when the activity is scheduled. DATE only: the model casts it to date (time of day dropped). Not related to DateTable. |
| `Activity Type` | varchar(100) | Type of activity (Design, Installation, Consultation, etc.). Values in the data: Design, Installation, Unspecified (Unspecified = CRM tasks such as calls and emails). |
| `Is First Appointment` | bigint | Indicator if this is the customer's first appointment. 1/0. |
| `Lead Name` | varchar(255) | Lead or prospect associated with the appointment. |
| `Lead Source Channel` | varchar(100) | Source channel where the lead originated. |
| `Related Entity` | varchar(255) | Entity or record this appointment is related to. |
| `Appointment Subject` | varchar(255) | Subject or title of the appointment. |
| `Assigned Staff Member` | varchar(255) | Staff member assigned to handle the appointment. |
| `Created By User` | varchar(255) | Person who created the appointment record. |
| `Appointment Start Time` | date | Start date and time of the appointment. DATE only: the model casts it to date (time of day dropped). Related to DateTable. |
| `Project Stage` | varchar(100) | Current stage of the project lifecycle. Unspecified, Sold, Lost, Design, Proposed, Cancelled, Incomplete ('Complete' never occurs). |
| `Appointment Location` | varchar(500) | Physical location where the appointment takes place. |
| `Account Manager` | varchar(255) | Account manager or owner responsible for the customer. |
| `Project Name` | varchar(255) | Project name or identifier. NULL for tasks and internal events. Joins `dim_projects`. Repeats across appointments. |
| `Customer Contact` | varchar(255) | Customer contact information. |
| `Appointment Notes` | text | Complete comments and notes about the appointment. |
| `Attached Resources` | text | Resources or materials attached to the appointment. |
| `Meeting Attendees` | text | Names of people attending the appointment. |
| `Project Status Category` | varchar(50) | Current status classification of the project. |
| `Activity Year-Month` | varchar(10) | Year and month of the activity for grouping. 'YYYY-MM'. |
| `Is Future Appointment` | varchar(10) | Indicates if the appointment is scheduled for the future. Text 'TRUE'/'FALSE', computed when Gold was last built, not live. |
| `Is Real Appointment` | varchar(5) | TRUE if Activity Type is not Unspecified AND Project Name is a real customer project (excludes designer availability blocks and internal CRM tasks like calls/emails). Text 'TRUE'/'FALSE' (not boolean). |
| `City` | varchar(100) |  |
| `State` | varchar(50) |  |
| `Zip Code` | varchar(20) |  |
| `Start Of WeekA` | date | DATE. Sunday on or before `Activity Date and Time` (not the start time). The trailing A is part of the name. |
| `appointment_sk` | bigint |  |
| `entity` | varchar(10) |  |
| `appointment_key` | bigint |  |
| `Data Ingested At` | datetime |  |
| `designer_sk` | bigint | Joins `dim_designers`.`designer_sk`. |
| `geography_sk` | bigint |  |
| `lead_source_sk` | bigint |  |
| `activity_type_sk` | bigint |  |
| `project_sk` | bigint |  |
| `entity_sk` | bigint |  |

### `Leads_Journey`

One row per lead (`Lead ID` unique) summarising its status-change history. Source: `icall_gold.fact_lead_journey`, all columns.

| Column | Type | Notes |
|---|---|---|
| `Lead ID` | varchar(50) | Unique. Joins `Leads`.`Lead ID`. |
| `Current Status` | varchar(100) |  |
| `Lead Journey` | varchar(2000) |  |
| `Total Status Changes` | bigint |  |
| `First Status Date` | datetime |  |
| `Latest Status Date` | datetime |  |
| `Latest File Date` | datetime |  |
| `Last Updated` | datetime |  |
| `journey_sk` | bigint |  |
| `entity` | varchar(10) |  |
| `Data Ingested At` | datetime |  |
| `lead_status_sk` | bigint |  |
| `entity_sk` | bigint |  |

### `Opportunities to Appointments`

Funnel bridge: one row per converted lead (`Lead ID` unique) with the project it became and its first appointment. Source: `icall_gold.fact_opportunities_to_appointments`, all columns, 2 date casts.

| Column | Type | Notes |
|---|---|---|
| `Index` | bigint | Reserved word: always write `Index`. |
| `Created Date` | date | DATE (cast by the model). Lead created date. |
| `Inspired Closets Lead Source` | varchar(255) |  |
| `Lead Status` | varchar(100) |  |
| `Converted Contact: Full Name` | varchar(255) |  |
| `Converted Date` | datetime |  |
| `Converted Project: Project Name` | varchar(255) | Joins `dim_projects`.`Project Name`. |
| `Activity Date and Time` | date | DATE (cast by the model). First appointment date. Relationship to DateTable is inactive. |
| `Activity Type` | varchar(100) |  |
| `Inspired Closets Lead Source.1` | varchar(255) |  |
| `Assigned` | varchar(255) |  |
| `Project Stage` | varchar(100) | Cancelled, Design, Lost, Proposed, Sold. |
| `Project` | varchar(255) |  |
| `entity` | varchar(10) | (hidden in the model) |
| `Lead ID` | varchar(50) |  |
| `Data Ingested At` | datetime |  |

### `Lead Milestones`

One row per lead: contact attempts, booked/studio/sold milestones and days to each, from the lead status history and the lead's project. Feeds the Opportunity Journey page. Source: the model's native SQL query over fact_leads, silver_lead_status_history and fact_sales.

| Column | Type | Notes |
|---|---|---|
| `Lead ID` | varchar(50) | Unique. Joins `Leads`.`Lead ID`. |
| `Contact Attempts` | bigint | Number of distinct status values containing 'Attempt' in the lead's status history (0 when none). |
| `Contact Path` | varchar(22) | 'Booked first try', 'Booked after follow-up', 'Not booked', 'No contact history' (leads created before 2025-01-01). Sort by `Contact Path Order`. |
| `Reached Studio` | int | 1/0. |
| `Journey Outcome` | varchar(100) | 'Sold', 'Proposed', 'Lost', 'Dead Lead' (from the lead's project), else 'Nurturing', 'Closed', 'Still working' (from Lead Status). Sort by `Journey Outcome Order`. |
| `Sold Amount` | decimal(18,2) | Amount of the lead's sold project, else NULL. |
| `Days to Scheduled` | decimal(23,2) | Days from lead created to the first 'Scheduled' status, 2 decimals. |
| `Days to Studio` | decimal(23,2) | Days from lead created to 'Moved to Studio' (or to the project's created date), 2 decimals. |
| `Days to Sold` | int | Days from lead created to the project's sold date (NULL unless the project is sold). |
| `Exclude From Journey` | int | 1 when Lead Status is Duplicate or Junk. |
| `Has Status History` | int | 1 when the lead was created on or after 2025-01-01. |
| `Contact Path Order` | int | (hidden in the model) |
| `Journey Outcome Order` | int | (hidden in the model) |
| `Studio Step` | varchar(18) | 'Reached the studio' or 'No studio visit'. |
| `Booked Appointment` | int | 1/0. (hidden in the model) |

### `dim_projects`

Distinct project names of this franchise; the hub that Salesforce_Data, Leads, Appointments, Opportunities to Appointments (and Projects Part List) relate to. Source: `icall_gold.dim_projects`, distinct `Project Name`.

| Column | Type | Notes |
|---|---|---|
| `Project Name` | varchar(255) |  |

### `dim_designers`

Designer roster: ACTIVE designers of this franchise only (`active_flag` = 1). `Designer` unique. Source: `icall_gold.dim_designers`, `active_flag` = 1, two renames.

| Column | Type | Notes |
|---|---|---|
| `Designer` | varchar(255) | Unique. Active designers only. Sort by `Sort`. |
| `Sort` | int | Display order of designers. (hidden in the model) |
| `entity` | varchar(10) | (hidden in the model) |
| `active_flag` | tinyint | Always 1 here. (hidden in the model) |
| `Total Projects` | int | (hidden in the model) |
| `Total Amount` | decimal(18,2) | (hidden in the model) |
| `designer_sk` | bigint |  |
| `first_seen_date` | date |  |
| `last_seen_date` | date |  |
| `Data Ingested At` | datetime |  |

### `DateTable`

Calendar, one row per day from 2020-01-01 to 2030-12-31. Created-date role (and lead, appointment dates). Source: `icall_gold.dim_date`, all rows.

| Column | Type | Notes |
|---|---|---|
| `Date` | date | DATE, unique. |
| `Year` | int |  |
| `Month` | varchar(10) | 'Jan'..'Dec'. Sort by `MonthNumber`. |
| `MonthNumber` | int |  |
| `Quarter` | varchar(10) | 'Q1'..'Q4'. |
| `Year-Month` | varchar(10) | 'YYYY-MM'. |
| `MonthYear` | varchar(20) | 'Nov 2026' style. Sort by `MonthNumber`. |
| `Weekday` | varchar(20) | 'Monday'..'Sunday'. Sort by `WeekdayNumber`. |
| `WeekdayNumber` | int | Monday = 1 .. Sunday = 7. |
| `YearQuarter` | varchar(20) | '2026 Q4' style. |
| `Start Of Week` | date | Sunday on or before `Date`. |
| `Data Ingested At` | datetime |  |

### `SoldDateTable`

Identical copy of DateTable (DAX calculated table `= DateTable`), used for the sold-date role. Source: view over DateTable.

| Column | Type | Notes |
|---|---|---|
| `Date` | date | DATE, unique. |
| `Year` | int |  |
| `Month` | varchar(10) | 'Jan'..'Dec'. Sort by `MonthNumber`. |
| `MonthNumber` | int |  |
| `Quarter` | varchar(10) | 'Q1'..'Q4'. |
| `Year-Month` | varchar(10) | 'YYYY-MM'. |
| `MonthYear` | varchar(20) | 'Nov 2026' style. Sort by `MonthNumber`. |
| `Weekday` | varchar(20) | 'Monday'..'Sunday'. Sort by `WeekdayNumber`. |
| `WeekdayNumber` | int | Monday = 1 .. Sunday = 7. |
| `YearQuarter` | varchar(20) | '2026 Q4' style. |
| `Start Of Week` | date | Sunday on or before `Date`. |
| `Data Ingested At` | datetime |  |

### `Lead Source Channels`

Distinct lead-source names (shared lookup, all franchises). Source: `icall_gold.dim_lead_sources`, distinct name.

| Column | Type | Notes |
|---|---|---|
| `Inspired Closets Lead Source` | varchar(100) |  |

### `Source_Sort`

Lead source with its display sort order (shared lookup). Source: `icall_gold.dim_lead_sources`, renamed columns.

| Column | Type | Notes |
|---|---|---|
| `Sort` | int |  |
| `Lead Source` | varchar(100) | Sort by `Sort`. |

### `LS Group Sort`

Lead-source group (3 groups) with its display sort order (shared lookup). Source: `icall_gold.dim_lead_sources`, one row per group.

| Column | Type | Notes |
|---|---|---|
| `Lead Source Group` | varchar(50) | Sort by `Sort`. |
| `Sort` | int |  |

### `dim_entity`

Franchise list: code, display name, region. Lists ALL franchises, like the model; holds no franchise data. Source: `icall_gold.dim_entity`, all rows, Title Case renames.

| Column | Type | Notes |
|---|---|---|
| `entity` | varchar(10) | Franchise code, e.g. ICLA. |
| `Display Name` | varchar(100) |  |
| `Region` | varchar(50) |  |
| `Is Active` | tinyint |  |
| `entity_sk` | bigint |  |
| `Data Ingested At` | datetime |  |

### `report_scorecard`

Weekly-report snapshot, long shape: one row per `metric_key` x `window_name` x `report_date`. Source: `icall_gold.report_scorecard` (weekly snapshot).

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | (hidden in the model) |
| `entity` | varchar(10) | (hidden in the model) |
| `report_date` | date |  |
| `metric_key` | varchar(50) | apv_sold, closing_rate, dead_leads_with_amount, lead_conversion, new_leads, open_pipeline_dollars, real_appt_pct, sold_count, sold_dollars. |
| `metric_label` | varchar(100) |  |
| `metric_kind` | varchar(20) | count, money or percent. |
| `window_name` | varchar(30) | current_week, prior_week, current_mtd, prior_mtd, current_ytd, prior_ytd. |
| `value` | decimal(18,4) |  |
| `created_at` | datetime | (hidden in the model) |

### `report_designer_leaderboard`

Weekly-report snapshot: one row per designer per `report_date`. Source: `icall_gold.report_designer_leaderboard` (weekly snapshot).

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | (hidden in the model) |
| `entity` | varchar(10) | (hidden in the model) |
| `report_date` | date |  |
| `designer_name` | varchar(255) |  |
| `sold_dollars_mtd` | decimal(18,2) |  |
| `sold_count_mtd` | int |  |
| `decided_count_mtd` | int |  |
| `total_opps_mtd` | int |  |
| `closing_rate_mtd` | decimal(8,6) |  |
| `sold_dollars_ytd` | decimal(18,2) |  |
| `rank_by_sold` | int |  |
| `created_at` | datetime | (hidden in the model) |

### `report_lead_source_roi`

Weekly-report snapshot: one row per lead-source group per `report_date`. Source: `icall_gold.report_lead_source_roi` (weekly snapshot).

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | (hidden in the model) |
| `entity` | varchar(10) | (hidden in the model) |
| `report_date` | date |  |
| `source_group` | varchar(100) |  |
| `sold_dollars_mtd` | decimal(18,2) |  |
| `opp_count_mtd` | int |  |
| `closing_rate_mtd` | decimal(8,6) |  |
| `rank_by_sold` | int |  |
| `created_at` | datetime | (hidden in the model) |

### `report_designer_concentration`

Weekly-report snapshot: one row per `report_date` (top-1/top-2 designer revenue concentration). Source: `icall_gold.report_designer_concentration` (weekly snapshot).

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | (hidden in the model) |
| `entity` | varchar(10) | (hidden in the model) |
| `report_date` | date |  |
| `top1_name` | varchar(255) |  |
| `top1_dollars_ytd` | decimal(18,2) |  |
| `top1_share` | decimal(8,6) |  |
| `top2_name` | varchar(255) |  |
| `top2_dollars_ytd` | decimal(18,2) |  |
| `top2_combined_share` | decimal(8,6) |  |
| `total_ytd` | decimal(18,2) |  |
| `active_designers_ytd` | int |  |
| `warn_flag` | tinyint(1) | 0/1 (boolean in the model). |
| `created_at` | datetime | (hidden in the model) |

### `report_channel_roi`

Weekly-report snapshot: one row per channel per `report_date`. Source: `icall_gold.report_channel_roi` (weekly snapshot).

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | (hidden in the model) |
| `entity` | varchar(10) | (hidden in the model) |
| `report_date` | date |  |
| `channel_name` | varchar(100) |  |
| `new_leads_mtd` | int |  |
| `sold_opps_mtd` | int |  |
| `sold_dollars_mtd` | decimal(18,2) |  |
| `decided_n` | int |  |
| `closing_rate` | decimal(5,4) |  |
| `rank_by_sold` | int |  |
| `created_at` | datetime | (hidden in the model) |

### `report_appointment_funnel`

Weekly-report snapshot: one row per activity type per `report_date`. Source: `icall_gold.report_appointment_funnel` (weekly snapshot).

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | (hidden in the model) |
| `entity` | varchar(10) | (hidden in the model) |
| `report_date` | date |  |
| `activity_type` | varchar(100) |  |
| `count_mtd` | int |  |
| `count_prior_mtd` | int |  |
| `mom_delta` | int |  |
| `share_of_mtd` | decimal(8,6) |  |
| `tied_to_sold_pct` | decimal(8,6) |  |
| `avg_cycle_days_mtd_sold` | decimal(8,2) |  |
| `hygiene_flag` | tinyint(1) | 0/1 (boolean in the model). |
| `hygiene_pct` | decimal(8,6) |  |
| `total_mtd_all` | int |  |
| `rank_by_count` | int |  |
| `created_at` | datetime | (hidden in the model) |

### `Projects Part List`

ICLA only. Closet-parts bill of materials: one row per part line per project (each project's current list, not history). Source: `icall_gold.icla_project_parts_list`.

| Column | Type | Notes |
|---|---|---|
| `id` | int | (hidden in the model) |
| `entity` | varchar(10) | (hidden in the model) |
| `project_name` | varchar(255) | Project this part belongs to (relates to dim_projects). Format: LastName_MMDDYY. Joins `dim_projects`.`Project Name`. |
| `category` | varchar(50) | Parts category from the Install Report: Components, Hardware, CTO (Cut-to-Order), Accessories, Fronts, Trim, Cabinet, Slatwall, Custom. |
| `original_item_id` | varchar(100) | Source item code from the Studio catalog (e.g. BP-WH = Back Panel White). |
| `original_description` | text | Raw part description from the Install Report (e.g. 'DW 7.4x24.5x3/4 SH WH'). |
| `quantity` | int | Number of this component in the project. |
| `sub_type` | varchar(50) | Sub-type detail (Fronts rows only). |
| `grain_type` | varchar(20) | Grain direction (Fronts rows only). |
| `dimensions` | varchar(100) | Raw dimensions string from the Install Report (e.g. '17.5x43.9x3/4'). |
| `profile` | varchar(50) | Front profile style (Fronts rows only). |
| `color` | varchar(50) | Raw color string from the Install Report (Fronts rows only). |
| `component_type` | varchar(30) | Human-readable component classification parsed from the description: Shelf, Vertical Panel, Drawer Front, Door, Screw, Hinge, Rod, Cam Fitting, Back Panel, etc. |
| `component_code` | varchar(10) | 2-3 letter component prefix code (SH=Shelf, VT=Vertical Panel, DW=Drawer, BP=Back Panel, TR=Trim Rail, WS=Wall Support). |
| `width_inches` | decimal(6,2) | Component width in inches (NULL for hardware items). |
| `height_inches` | decimal(6,2) | Component height in inches (NULL for hardware items). |
| `depth_fraction` | varchar(20) | Component depth as a fraction string ('3/4' most common, also '3/8', '1'). |
| `color_code` | varchar(5) | 2-3 char color code (WH=White, DA=Dark/Graphite, MP=Maple, MNL=Manila Nickel, SG=Shaker Grey, BK=Black). |
| `color_name` | varchar(30) | Human-readable color parsed from the description: White, Dark/Graphite, Maple, Manila Nickel, Shaker Grey, Matte Black, Gray (NULL for most hardware). |
| `panel_type` | varchar(20) | Panel mounting type: Fixed, Adjustable, Extra Deep, Radius (NULL for non-panel items). |
| `panel_orientation` | varchar(20) | Panel orientation: Left Hand, Right Hand, Full Depth, Left/Right/Front Notch, No Notch. |
| `material` | varchar(30) | Material parsed from the description: Melamine 2mm Edge, Maple, Steel, Zinc Plated, MDF (NULL for many rows). |
| `pack_size` | int unsigned | Pack size for bulk hardware items (e.g. 100 from '(100 pk)'). |
| `is_adjustable` | tinyint | 1 if the shelf/panel is adjustable. |
| `is_fixed` | tinyint | 1 if the shelf/panel is fixed position. |
| `has_soft_close` | tinyint | 1 if the part has the soft-close feature (drawer slides / drawer fronts). |
| `source_created_at` | datetime | When the source scrape produced this row (Install Report download time). |
| `data_ingested_at` | datetime | When the row landed in icall_gold (data freshness indicator). |
