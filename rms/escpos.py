"""ESC/POS thermal printing: builds the raw bytes for a receipt or kitchen ticket and sends them to a network printer
(TCP port 9100). Works with Epson / Xprinter / Rongta / most 58mm & 80mm LAN printers. Text is printed as plain ASCII
(thermal printers have no Urdu font) — the browser receipt remains available for full Unicode."""
import socket
import unicodedata

ESC, GS = b"\x1b", b"\x1d"


class PrinterError(Exception):
    pass


def ascii_text(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(ch if ord(ch) < 128 else "?" for ch in s if not unicodedata.combining(ch))


class Doc:
    def __init__(self, width=42):
        self.w = max(24, int(width))
        self.b = bytearray(ESC + b"@")               # initialise

    def raw(self, data):
        self.b += data
        return self

    def align(self, mode):                            # left / center / right
        return self.raw(ESC + b"a" + bytes([{"left": 0, "center": 1, "right": 2}[mode]]))

    def bold(self, on=True):
        return self.raw(ESC + b"E" + bytes([1 if on else 0]))

    def size(self, w=1, h=1):                         # 1..4 multiples
        return self.raw(GS + b"!" + bytes([((w - 1) << 4) | (h - 1)]))

    def text(self, s="", **kw):
        self.raw(ascii_text(s).encode("ascii") + b"\n")
        return self

    def hr(self, ch="-"):
        return self.text(ch * self.w)

    def lr(self, left, right):
        left, right = ascii_text(left), ascii_text(right)
        room = self.w - len(right) - 1
        lines = []
        while len(left) > room:
            cut = left.rfind(" ", 0, room)
            cut = cut if cut > 0 else room
            lines.append(left[:cut])
            left = left[cut:].lstrip()
        lines.append(left)
        for ln in lines[:-1]:
            self.text(ln)
        pad = max(1, self.w - len(lines[-1]) - len(right))
        return self.text(lines[-1] + " " * pad + right)

    def qr(self, data, size=6):
        d = ascii_text(data).encode("ascii")
        n = len(d) + 3
        self.raw(GS + b"(k" + bytes([4, 0, 0x31, 0x41, 0x32, 0]))           # model 2
        self.raw(GS + b"(k" + bytes([3, 0, 0x31, 0x43, size]))             # module size
        self.raw(GS + b"(k" + bytes([3, 0, 0x31, 0x45, 0x31]))             # error correction M
        self.raw(GS + b"(k" + bytes([n & 255, n >> 8, 0x31, 0x50, 0x30]) + d)
        self.raw(GS + b"(k" + bytes([3, 0, 0x31, 0x51, 0x30]))             # print
        return self

    def feed(self, n=1):
        return self.raw(b"\n" * n)

    def cut(self):
        return self.raw(b"\n\n\n" + GS + b"V" + bytes([1]))                 # partial cut

    def bytes(self):
        return bytes(self.b)


def _money(v):
    return f"{v:,.2f}"


def build_receipt(order, lines, payments, width=42, feedback_url=""):
    b = order.branch
    d = Doc(width)
    d.align("center").bold().size(2, 2).text(b.name).size(1, 1).bold(False)
    if b.address:
        d.text(b.address)
    if b.phone:
        d.text(b.phone)
    d.hr()
    d.bold().text("BILL / INVOICE" if order.bill_number else "PROVISIONAL BILL").bold(False).align("left")
    if order.bill_number:
        d.lr("Bill no.", order.bill_number)
    d.lr("Order", order.number)
    d.lr("Type", order.get_order_type_display() + (f" {order.table.name}" if order.table_id else ""))
    d.lr("Date", (order.billed_at or order.created_at).strftime("%d/%m/%Y %H:%M"))
    if order.waiter_id:
        d.lr("Waiter", order.waiter.display_name)
    if order.customer_name:
        d.lr("Customer", order.customer_name)
    d.hr()
    for l in lines:
        d.lr(f"{l.qty} x {l.name}", _money(l.line_total))
    d.hr()
    d.lr("Subtotal", _money(order.subtotal))
    if order.discount:
        d.lr("Discount", "-" + _money(order.discount))
    if order.service_charge:
        d.lr("Service", _money(order.service_charge))
    if order.tax:
        d.lr("Tax", _money(order.tax))
    if order.delivery_fee:
        d.lr("Delivery", _money(order.delivery_fee))
    d.bold().size(1, 2).lr(f"TOTAL ({b.currency_code})", _money(order.total)).size(1, 1).bold(False)
    for p in payments:
        d.lr(("Refund " if p.amount < 0 else "Paid ") + p.get_method_display(), _money(p.amount))
        if p.tip:
            d.lr("  Tip", _money(p.tip))
        if p.change:
            d.lr("  Cash / change", f"{_money(p.tendered)} / {_money(p.change)}")
    d.lr("Status", "PAID" if order.is_paid else ("PARTIAL" if payments else "UNPAID"))
    d.hr().align("center").text("Thank you!")
    if feedback_url:
        d.text("Rate your meal - scan:").qr(feedback_url, 5)
    d.align("left").cut()
    return d.bytes()


def build_kot(order, lines, rnd, width=42):
    d = Doc(width)
    d.align("center").bold().size(2, 2).text("KITCHEN ORDER").size(1, 1)
    d.text(f"{order.number}" + (f"  (round {rnd})" if rnd > 1 else "")).bold(False)
    d.align("left").hr("=")
    d.bold().size(1, 2).text(order.get_order_type_display().upper() + (f"  TABLE {order.table.name}" if order.table_id else "")).size(1, 1).bold(False)
    d.lr("Time", order.created_at.strftime("%H:%M"))
    if order.waiter_id:
        d.lr("Waiter", order.waiter.display_name)
    d.hr()
    for l in lines:
        d.bold().size(1, 2).text(f"{l.qty} x {l.name}").size(1, 1).bold(False)
        if l.notes:
            d.text("   >> " + l.notes)
    d.hr("=")
    if order.notes:
        d.text("NOTE: " + order.notes)
    d.align("left").cut()
    return d.bytes()


def send(host, port, data, timeout=4):
    if not host:
        raise PrinterError("No printer IP is set for this branch (Branches → edit → printer).")
    try:
        with socket.create_connection((host, int(port or 9100)), timeout=timeout) as s:
            s.sendall(data)
    except OSError as e:
        raise PrinterError(f"Cannot reach printer {host}:{port} — {e}")
