# Restopro — Restaurant Management System + POS (Django)

Multi-branch restaurant ERP + POS. Light premium theme, dine-in / takeaway / delivery, waiters, riders,
kitchen board, supply chain, online-order integrations, country-wise time zone / currency / language.

## Run (Windows / Linux / macOS)

```bash
python -m venv venv
venv\Scripts\activate            # Windows   (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo       # demo data: 3 branches, menu, tables, riders, stock, orders
python manage.py runserver
```
Open http://127.0.0.1:8000/ — online ordering page: http://127.0.0.1:8000/order/

| Login | Password | Access |
|---|---|---|
| `admin` | `admin123` | Owner — everything, all branches |
| `khi_manager` | `demo1234` | Manager of the **MASTER** branch — can view/manage every branch |
| `lhe_manager`, `dxb_manager` | `demo1234` | Branch managers — own branch only |
| `khi_cashier`, `khi_waiter1` | `demo1234` | Cashier / Waiter |
| `khi_kds`, `lhe_kds` | `demo1234` | **Kitchen Display / Head Chef** — separate kitchen account: sees the whole kitchen board (all chefs + queue + ready), marks any order ready, no POS / payments; never receives orders itself |
| `khi_kitchen`, `khi_kitchen2`, `lhe_kitchen` | `demo1234` | Chefs (Karachi has 2 chefs, Lahore 1 → queue visible in demo) |

Change all passwords before going live (or skip `seed_demo` and create your own data).

## What is included

* **Multi-branch + Master branch** — exactly one branch can be "Master". Owner and Master-branch managers get a branch
  switcher (All branches / any single branch) and a cross-branch dashboard, stock matrix, reports. Everyone else is locked
  to their own branch (enforced in every query, not just hidden in the menu).
* **POS** — touch-friendly (big tap targets, quantity badges on tiles, tablet drawer menu), item photos
  (upload in *Menu → Edit*; items without a photo show a coloured illustration); dine-in (table + waiter), takeaway, delivery (customer + rider); discount, per-branch tax, service charge
  (dine-in only), delivery fee; "Place order" (pay later) or "Pay now" (cash/card/online/wallet); printable receipt.
* **Auto billing** — the moment an order is completed a bill/invoice number (`INV-<BRANCH>-000001`, sequential per branch) is generated
  automatically and the receipt opens; *Bills* page lists all bills with date/type/payment filters, totals and reprint. Old completed orders are back-filled on migrate.
* **Table management** — floor view by area, live status (free / occupied / reserved), open order per table.
* **Smart kitchen queue** — every order is sent to a chef automatically the moment it is placed and a live
  **HH:MM:SS timer** starts (turns amber / red when it exceeds the item's prep time). Each chef cooks at most
  **3 orders at a time** (configurable per branch: *Branches → Kitchen capacity*). With several chefs the order goes to the
  least-loaded one; when all are full, new orders wait in the **Queue** (#1, #2 … with their own waiting timer) and move
  to a chef's screen automatically, first-come-first-served, the moment a chef taps *Mark ready* (or an order is cancelled).
  The chef screen refreshes itself every 5 s and beeps on new orders. Branches with no chef account start orders immediately.
  Chefs are users with role **Chef (Kitchen)**; each chef only sees and controls their own orders.
* **Waiters & riders management** — waiter accounts with order counts; riders per branch with live status
  (available → on delivery → available automatically).
* **Supply chain** — suppliers, ingredients, per-branch stock with reorder alerts, purchase orders + goods receiving,
  **inter-branch stock transfers**, wastage / adjustments, full stock history, **recipes** (ingredients are checked and deducted
  automatically **the moment an order is placed** — a burger order removes bun, patty, cheese… per its recipe; if an ingredient is short the order is refused
  with the ingredient name (Settings → *Block orders when ingredients are short*); cancelling before cooking returns the stock).
* **Ingredient module** — ingredients have category, unit, cost, supplier and an alert level (per branch, or a default on the ingredient).
  *Ingredients* page: live stock with meter, Low / Out status, "used in N dishes", stock value; ingredient page: stock per branch, dishes that use it
  (with portions still possible), 7-day usage, movements, quick adjust, "add to dish". *Recipes* page: every dish with ingredient count, cost per portion,
  margin, food-cost %, and whether the branch stock can make it (dishes without a recipe are flagged). Recipe editor: live cost/margin, per-branch
  availability, copy recipe from another dish.
* **Stock alerts when a dish is ordered** — POS tiles show *Low stock / Only N left / Sold out*; tapping a dish whose ingredient is low pops a warning
  naming the ingredient and what is left; the POS shows a banner of all low ingredients; after the order is placed a *Stock alert* message tells staff
  which ingredients just dropped below the alert level. If an ingredient is short the order is refused naming it (Settings → block when short).
* **Order integrations** — channels (POS, Website, Foodpanda-style apps, WhatsApp, phone…) each with an API key and
  commission %; public REST endpoints for apps; your own public ordering website with order tracking.
* **Country-wise settings** — pick a country on a branch and time zone, currency and language are filled in
  automatically (25 countries preset, all editable). Dates are shown in the branch time zone, money in the branch currency.
* **Language** — English / اردو / العربية with RTL. **Optional:** Settings → "language selection" ON lets each user pick;
  OFF locks every screen to the branch language.
* **Branch menu** — global menu, but each branch can switch items off and set its own price.
* **Reports** — sales by day / type / source (with app commission) / branch, top items, waiter and rider performance.

## Standard restaurant flow (end to end)

```
Guest seated (Reservation → Seat)  →  waiter / cashier takes order on POS  →  KOT printed, auto-sent to a chef (timer, max 3 per chef, queue)
→ guest orders more? "Add items" = new KOT round (only new items shown to the chef) → Ready → Served (waiter)
→ "Request bill" (table turns amber) → cashier takes payment (cash / card / online / wallet, mixed, tip, change, split bill)
→ order Completed → bill INV-<BRANCH>-000001 generated + receipt → table goes to Cleaning → "Cleaned" → Free
```

* **Table states** — Free · Occupied · Bill requested · Cleaning · Reserved (+ today's reservation badge). Move an order to another table.
* **KOT** — printable kitchen ticket per round (Settings → *auto print KOT* opens the print dialog automatically). Items added later are marked **NEW / KOT 2** on the chef screen.
* **Void & cancel** — mandatory reason; before cooking the stock returns, after cooking it is logged as wastage and needs a **manager** (own role, or manager username + password typed by the cashier). Waiters cannot void. Cancelling a paid order refunds the payments. Everything is in *Voids & Cancels*.
* **Payments** — several payments per bill (cash + card, one per guest), tip, cash-change calculator, "split equally ÷2…÷5", and **Split bill by items** (creates `<order>-S1` on the same table).
* **Cashier shifts** — open with opening cash, close with counted cash → expected vs counted difference + printable Z-report. Settings → *require shift* forces cashiers to open one before taking payments.
* **Day close** — end-of-day summary (sales, tax, tips, methods, voids). Closing is blocked while orders or shifts are open and then blocks new orders until a manager reopens the day.
* **Supply chain** — *Stock → Order low stock* creates draft POs per supplier (default supplier on the ingredient), approve & send, **partial goods receiving (GRN)** with supplier invoice no., pending quantities tracked. POS tiles show **Sold out / Only N left** from the recipe stock.
* **Reservations** — book by date/time/guests/table, Seat / No-show / Cancel; seating opens the table's order screen.

## Integration API (delivery apps, other websites)

```
POST /api/v1/orders/            header: X-API-Key: <channel key>
{
  "branch": "KHI", "external_id": "FP-10234", "order_type": "delivery",
  "customer": {"name": "Ali", "phone": "0300...", "address": "DHA Ph 6"},
  "paid": true, "payment_method": "online",
  "items": [{"sku": "BRG-01", "qty": 2}, {"name": "Special combo", "qty": 1, "price": 950}]
}
GET  /api/v1/menu/?branch=KHI              branch menu with SKUs and prices
GET  /api/v1/orders/<order_number>/        order status
```
Same `external_id` twice returns the existing order (safe retries). Keys are managed under *Channels & Apps*.

## Production notes

* `SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` via environment variables.
* Database: SQLite by default. For PostgreSQL / MySQL set `DB_ENGINE=postgresql|mysql`, `DB_NAME`, `DB_USER`,
  `DB_PASSWORD`, `DB_HOST`, `DB_PORT` (and `pip install psycopg[binary]` or `mysqlclient`).
* Uploaded item photos go to `media/` — serve `/media/` with nginx / your web server in production (Django only serves it in DEBUG).
* `python manage.py collectstatic` then run with gunicorn / waitress behind nginx / IIS.
* Run tests: `python manage.py test rms`.
* Adding a language: add it to `rms/countries.py → LANGUAGES` and a dictionary in `rms/i18n.py`
  (missing strings fall back to English).

## Not included yet (good next phases)

Payment-gateway integration, ESC/POS thermal-printer direct printing, customer/loyalty database, push/WebSocket
true websocket push (kitchen board polls every 5 s), rider mobile app with GPS, accounting ledger, native Foodpanda/Careem
connectors (they need each company's partner credentials — the REST API above is the bridge).

## Demo data (`python manage.py seed_demo --force`)
Covers every module: 3 branches (Master Karachi), 7 days of history, closed cashier shifts with cash differences + an open shift
per branch, 6 closed days (Day Close), multi/mixed payments with tips & change, split bill, KOT round 2, served / bill-requested /
cleaning tables, item voids, cancelled + refunded orders, delivery (out on road / waiting), reservations, purchase orders
(draft / ordered / partial / received / cancelled), stock transfers, wastage & count corrections, and low / out-of-stock
ingredients so alerts show. **`--force` wipes all existing data.**

## Staff performance, menu analytics, AI insights
- **Staff Performance** (`/performance/`): every waiter and rider — tables served / deliveries, what the hotel earned from them
  (sales minus tax), gross profit (after recipe cost), tips and average bill. Click a name for the **profile** (daily chart,
  top items, recent orders, rank, best day, voids, avg handover time for riders). Waiters see their own profile under **My Stats**.
- **Menu Analytics** (`/analytics/menu/`): cost per item from recipes, margin, food-cost %, price needed for a target food-cost %,
  top sellers, most profitable items, recipe preview popup, and menu-engineering classes (Star / Plowhorse / Puzzle / Dog).
- **AI Insights** (`/analytics/ai/`): 7-day sales forecast (weekday seasonality x trend, with back-test accuracy), expected item
  demand, ingredient run-out + purchase advice, busy hours and written insights. Works offline. Optional "Ask AI advisor" button
  uses Claude if `ANTHROPIC_API_KEY` is set (optionally `RESTOPRO_AI_MODEL`); only aggregated numbers are sent.
- Branch field **cost_factor** converts head-office ingredient cost to the branch currency (Dubai demo: 0.01429).
- Run `python manage.py migrate` after updating (new migration 0009).

## Customer feedback (automatic)
- Every bill / receipt carries a **QR code + link** (`/rate/<token>/`, no login). The customer rates each dish (1–5★, optional comment),
  the waiter / rider / service, and can write a comment (English or Roman Urdu). The same link shows on the order page and the track page.
- **Customer Feedback** page: service / food rating, satisfaction %, rating spread, sentiment, themes (taste, temperature, freshness,
  portion, speed, service, hygiene, price), rating per item (complaints + praise), waiters, riders, low-rating alerts.
  The **AI analysis** runs automatically on every page load (keyword sentiment, English + Roman Urdu). "Deeper AI report (Claude)" is
  optional and needs `ANTHROPIC_API_KEY`.
- Waiter / rider profile: customer rating, rating spread, praise / complaint themes and comments. Waiter profile also has
  **Earnings by table** (what every table he served brought in).
- Menu Analytics has a Customer rating column; AI Insights includes feedback findings; the dashboard shows low ratings of the last 3 days.
- Needs `pip install -r requirements.txt` (adds `segno` for the QR code) and `python manage.py migrate` (0010).

## Customer ordering site (premium)

`/order/` -> branch -> menu with search, category chips, photo cards, sticky cart (bottom-sheet on mobile), order tracking with live progress steps. Styles: `static/rms/site.css` (light + gold). Same endpoints as before.

## Crystal-report tables

Every data table (`table.tbl`) gets a crystal-report look, search box, per-column filters, sortable headers, pagination (10/15/25/50/100/All), grand totals, Print (with business/branch/user/date banner) and CSV export - all client-side via `static/rms/report.js`. Opt-outs: `data-plain`, `data-nosearch`, `data-server` (server-paginated pages keep their own filters and show a Page total), `th[data-sum]` for totals. Tables under 5 rows get the styling only.

## Income, customers & loyalty, online payment, thermal printing
- **Salary / commission / total income:** waiter (and rider) forms have monthly salary, commission % (riders also per-delivery pay).
  Profiles and Staff Performance show total income = prorated salary + commission + per-delivery pay + tips.
- **Customers** (`/customers/`): created automatically from the order phone number (matched on the last 10 digits, so `0300…` and
  `+92 300…` are the same person). Visits, spend, tier (Bronze / Silver 6+ / Gold 15+), favourite items, ratings, points history.
  **Loyalty:** points on completed orders (Settings: points per 100, value of a point); redeem on the order page before payment;
  points return if the order is cancelled; managers can adjust points manually.
- **Online payment:** Settings → Payment gateway. *Sandbox* is a built-in test gateway. *Stripe Checkout* needs env vars
  `STRIPE_SECRET_KEY` and (for the webhook `/webhooks/stripe/`) `STRIPE_WEBHOOK_SECRET`. Create a link on the order page
  (QR + URL) or the customer taps "Pay online" on the track page. A paid link becomes a normal *online* payment.
- **Thermal printer (ESC/POS, 58 / 80 mm):** Branches → edit → printer IP / port, paper width. Order page: *Print KOT* / *Print bill*
  send raw ESC/POS to the LAN printer (port 9100); *.bin* downloads work with RawBT or any print agent. With *Auto-print KOT* on and
  a printer IP set, new KOT rounds print straight to the kitchen printer. The server must be on the same network as the printer
  (run Restopro on the restaurant PC). Thermal text is ASCII (no Urdu glyphs); the browser receipt keeps full Unicode.
- Run `python -m pip install -r requirements.txt` and `python manage.py migrate` (0011).

## Deploy on Vercel
Vercel is serverless: no permanent disk, so SQLite will not work - use PostgreSQL (free Neon DB: neon.tech, or Vercel Storage > Neon).
1. Create a Postgres DB and copy its connection string (`postgresql://user:pass@host/db?sslmode=require`).
2. Create tables + demo data **from your PC** (once):
   - Windows: `set DATABASE_URL=postgresql://...` then `pip install -r requirements.txt`, `python manage.py migrate`, `python manage.py seed_demo` (or `createsuperuser`/your own data).
3. Push the folder to GitHub, import it in Vercel (Framework: Other). Environment variables: `DATABASE_URL`, `SECRET_KEY` (long random), optional `ANTHROPIC_API_KEY`, `STRIPE_*`.
4. Deploy. Static files are pre-built in `staticfiles/` (re-run `python manage.py collectstatic --noinput` after CSS/JS changes).
Notes: uploaded menu photos are stored in /tmp on Vercel (not permanent) - use external storage (Vercel Blob / S3) or the built-in colour illustrations; thermal printing over TCP 9100 cannot reach a shop printer from Vercel (use the browser print or run Restopro locally for ESC/POS).

## Chef: done orders, stock and stock requests
Kitchen screen shows per chef: orders done today, average cooking time, cooking now, all-time. Below it the branch stock (lowest first, Low/Out badges) and a *Need more stock?* form (one click "Request" fills the suggested quantity; mark Urgent). Manager: *Supply Chain -> Stock Requests* - edit qty, **Approve** (adds the item to the supplier's draft purchase order, creating it if needed) or **Reject**. The chef sees Waiting / Approved (PO number) / Rejected. Needs `python manage.py migrate` (migration 0012).

## Bell notifications
Top-bar bell with red unread count, dropdown list, "Mark all read", beep + pop-up within ~6 s (no refresh). New / urgent stock request -> every manager of that branch, master-branch managers and the owner; approve / reject (with the manager's reply) -> the chef who asked. Other branches are not alerted. Helper: `services.notify(users, title, body, url, kind)`. Needs `python manage.py migrate` (0013).

## Automations (Automations page, owner / master manager)
Everything runs by itself and each item has an on/off switch. The engine (`rms/automation.py`) wakes up from the bell poll of any logged-in user (max once a minute), from `python manage.py run_automations` (Windows Task Scheduler every 5 min) or from `GET /cron/tick/?key=CRON_SECRET` (Vercel Cron, daily 18:00 UTC in `vercel.json`; Pro plans can run it every 5 min).
* **Alerts (bell):** order cooking past its prep time / waiting too long in the queue, low customer rating, table stuck in Cleaning, shift left open. Each is alerted once.
* **Auto reorder:** once a day, draft POs for ingredients at/below alert level. **Smart ordering:** forecast (sales history x recipes) for the next 7 days vs stock -> tick, edit qty, create draft POs.
* **Rider auto-assign:** a ready delivery order goes to the least-busy available rider (manager alert if nobody is free).
* **Daily report:** after the closing hour each branch gets a summary (sales, types, payments, voids, top items, cook time, rating, stock) -> bell + email + WhatsApp/SMS to managers; see *Daily Reports*.
* **Customer messages:** order received / ready / on the way / thank-you + feedback link. **Supplier:** approved PO is sent by email / WhatsApp. **Win-back:** customers away for N days get an offer (once per 60 days).
* **Happy hour pricing:** rules by hour + weekday (+ AI suggestions from quiet hours); price changes everywhere (POS, website, apps, bills).
* **Salary slips:** on the 1st last month's slips are prepared, managers get a bell alert and staff a message; printable slip per person (*Salary Slips*).
* **Delivery-app menu sync:** set a webhook URL on a channel; menu / price / availability changes are POSTed (HMAC-signed with the channel API key).
* Messages are **recorded only** until keys exist. Environment variables: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_FROM`, `TWILIO_WHATSAPP_FROM` (or `MESSAGE_WEBHOOK_URL`), `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `SITE_URL` (links in messages), `CRON_SECRET`. Needs `python manage.py migrate` (0014).

## AI Centre (pro level AI)

Open **AI Centre** in the menu. Every AI feature works in two modes: with `ANTHROPIC_API_KEY` set (Vercel env var, or `set ANTHROPIC_API_KEY=...` on Windows) Claude answers; without a key a built-in rule-based version answers, so nothing ever breaks. Optional `RESTOPRO_AI_MODEL` changes the model. Every call is logged on the AI Centre page (Claude vs rules, time, errors).

- **AI Copilot** - the "Ask AI" button on every manager page. Ask in Roman Urdu or English: "aaj ki sale kitni hai?", "kaunsa stock kam hai?", "kal ki forecast?".
- **AI order taking** - POS and the customer website have an AI box: "do zinger burger aur teen fries" fills the cart (typos, Urdu number words, fuzzy menu match).
- **WhatsApp ordering** - tick it in AI Centre, point the Twilio WhatsApp "incoming message" webhook (POST) to `https://YOUR-SITE/ai/whatsapp/` (add `?key=...` if you set `WHATSAPP_WEBHOOK_KEY`). The customer sends the order, the bot replies with a summary, YES creates the order.
- **Smart upsell** - "Goes well with this" chips in POS and on the website, from your own order history.
- **Menu advisor** - raise price / promote / remove suggestions with a one-click "Set price" (branch price override).
- **Reply drafts** - polite Roman Urdu apology for each unhappy customer, edit and send.
- **Tomorrow prep plan** - what to prep, what to buy, busy hours.
- **Anomaly watch** - runs with the automations: many voids by one person, unusual void amount, discounts over 15%, sales far below the usual weekday, stock write-offs; alerts go to the managers' bell.
- Daily report starts with an AI summary line when Claude is connected.

After updating run `python manage.py migrate` (migration 0015).

## Roles (8) and their demo logins

Password for every demo user: `demo1234` (owner `admin` / `admin123`). Open **Roles & Access** in the menu for the live matrix.

| Role | Logins | Does |
|---|---|---|
| Owner | admin | everything, all branches, settings, staff, AI/automations |
| Manager | khi_manager (master, all branches), lhe_manager, dxb_manager | runs a branch: menu, prices, reports, voids, purchasing, day close |
| Cashier | khi_cashier, lhe_cashier, dxb_cashier | POS, orders, bills, shift, assigns riders |
| Waiter | khi_waiter1/2, lhe_waiter1/2, dxb_waiter1/2 | dine-in orders (own only), tables, reservations, My Stats |
| Chef | khi_kitchen, khi_kitchen2, lhe_kitchen, dxb_kitchen | own cooking queue, requests stock |
| Head chef / KDS | khi_kds, lhe_kds, dxb_kds | whole kitchen board |
| Storekeeper | khi_store, lhe_store, dxb_store | stock, ingredients, suppliers, purchase orders, approves chef requests (no sales data) |
| Delivery rider | khi_rider1/2/3, lhe_rider1/2, dxb_rider1/2 | My Deliveries: picked up, delivered + cash collected, earnings |

Every role lands on its own page after login, sees only its menu, and (except owner / master manager) only its own branch. Everyone has Change password and, except owner/manager, My Salary Slip. Creating a user with role Delivery Rider automatically creates the linked rider profile. After updating run `python manage.py migrate` (0016), or create a rider/storekeeper with `python manage.py create_user NAME PASS --role rider --branch KHI`.

## Online order acceptance

Website and WhatsApp orders no longer go straight to the kitchen. They appear under **Online Orders** (cashier / manager) with a bell and sound: choose **Accept** (ready in 15-60 min) or **Reject** (with a reason). Accepting sends it to the kitchen and messages the customer; rejecting cancels it, returns the stock and tells the customer why (the tracking page shows it too). If nobody answers within N minutes managers get an alert, or the order is auto-accepted. Settings: Automations > Online orders (turn the whole step off to get the old behaviour). Run `python manage.py migrate` (0017).

## Security: audit log, login lockout, install on phone
- **Audit Log** (owner / master manager): who created, changed or deleted branches, menu, prices, recipes, staff, settings, promo codes, expenses, modifiers, plus every login, logout and failed login (with IP).
- **Login lockout:** 5 wrong passwords for one username within 15 minutes blocks that username for the rest of the 15 minutes (the correct password is refused too, until it passes).
- **Install as an app (PWA):** open the site in Chrome / Safari on the phone → "Add to Home screen". It has an icon, runs full screen and shows an offline page if the network is down. (Orders themselves still need internet.)

## Photos, export, backup, import
- **Menu photos are stored in the database** (resized to 900 px JPEG), so they work on Vercel where the disk is read-only. Upload them in Menu Items.
- **Backup & Import** page: download CSV of orders, order lines, customers, menu, ingredients, stock, suppliers, staff, audit log. **Full backup** (JSON of all data) is for the owner / master manager only. Import menu items and ingredients from CSV (a sample file can be downloaded; existing names are updated, not duplicated).

## Expenses, Profit & Loss, promo codes
- **Expenses** (manager): rent, utilities, repairs, marketing, packaging, transport, licences. Each branch sees only its own.
- **Profit & Loss** (owner / manager, pick dates): Sales (without tax) − food cost (from recipes) = Gross profit; − expenses − salaries (active staff and rider monthly salaries × days/30) = Net profit.
- **Promo Codes** (owner): percent or fixed amount, minimum order, maximum discount, valid dates, usage limit, one branch or all. Customers type the code on the website; cashiers type it in the POS (Apply button shows the saving). The code is checked again on the server when the order is placed. Demo codes: `WELCOME10`, `FLAT100` (min 1000), `LHE20` (Lahore only).

## Modifiers (size, extras, spice level)
Create groups in **Modifiers** (one option per line: `Large | 150`). Mark a group *required* (customer must choose) and/or *several allowed* (extras). Attach groups to a dish in **Menu Items → Modifier groups**. In the POS and on the website, tapping such a dish opens a chooser; the price includes the extras, and the choice is printed on the KOT, kitchen screen, bill and receipt (as "+ Large, Extra cheese"). Prices and rules are verified on the server. Demo data has Extras and Spice level on burgers/pizzas and Drink size on drinks.
