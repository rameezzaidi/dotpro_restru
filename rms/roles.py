"""Single place that documents what every role is for (used by the Roles & Access page and the tests)."""
ROLE_INFO = [
    ("owner", "Owner", "Whole business: every branch, settings, branches, staff, reports and automations.",
     ["All pages, all branches", "Create branches and users", "Settings, AI and automations"]),
    ("manager", "Manager", "Runs a branch (the master-branch manager sees every branch).",
     ["Menu, prices, recipes, staff", "Reports, analytics, feedback, voids approval", "Purchasing, stock requests, day close"]),
    ("cashier", "Cashier", "Counter: takes orders, collects payments and prints bills.",
     ["POS, orders, bills, customers", "Own cash shift and day-end count", "Assigns riders, reservations"]),
    ("waiter", "Waiter", "Floor: tables and dine-in orders, own performance.",
     ["POS for dine-in, own orders only", "Tables and reservations", "My Stats and own salary slip"]),
    ("kitchen", "Chef", "Cooks the orders assigned to them.",
     ["Own cooking queue and timers", "Marks orders preparing / ready", "Sees stock and requests ingredients"]),
    ("kds", "Head Chef / Kitchen Display", "Runs the whole kitchen board of the branch.",
     ["Full kitchen board and queue", "Marks orders preparing / ready", "Sees stock and requests ingredients"]),
    ("storekeeper", "Storekeeper", "Inventory and purchasing; no sales data.",
     ["Stock, ingredients, suppliers", "Purchase orders and receiving", "Approves chef stock requests, transfers, smart ordering"]),
    ("rider", "Delivery Rider", "Delivers orders assigned to them.",
     ["My Deliveries only", "Marks picked up / delivered and cash collected", "Sees own earnings today"]),
]
LANDING = {"kitchen": "kitchen", "kds": "kitchen", "rider": "rider_home", "storekeeper": "stock"}
