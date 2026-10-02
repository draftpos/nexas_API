from frappe import _
import frappe
import base64
from tzlocal import get_localzone
import json


# Helpers
def generate_keys(user):
    api_key = user.get('api_key')
    api_secret = None
    try:
        api_secret = user.get_password('api_secret')
    except Exception:
        api_secret = None

    if not api_key or not api_secret:
        if not api_key:
            api_key = frappe.generate_hash(length=15)
            user.api_key = api_key
        api_secret = frappe.generate_hash(length=15)
        user.set_password('api_secret', api_secret)
        user.save(ignore_permissions=True)
        frappe.db.commit()

    return {"api_secret": api_secret, "api_key": api_key}


@frappe.whitelist(allow_guest=True)
def login(usr, pwd, timezone=None):
    local_tz = str(get_localzone())
    erpnext_tz = frappe.utils.get_system_timezone()

    if timezone and timezone != erpnext_tz:
        frappe.local.response.http_status_code = 400
        frappe.local.response["message"] = f"Timezone mismatch. Your timezone is {timezone}, but system requires {erpnext_tz}"
        return

    try:
        login_manager = frappe.auth.LoginManager()
        login_manager.authenticate(user=usr, pwd=pwd)
        login_manager.post_login()
    except frappe.exceptions.AuthenticationError:
        frappe.clear_messages()
        frappe.local.response.http_status_code = 422
        frappe.local.response["message"] = "Invalid Email or Password"
        return

    user = frappe.get_doc('User', frappe.session.user)
    api_generate = generate_keys(user)
    token_string = f"{api_generate['api_key']}:{api_generate['api_secret']}"
    auth_header = f"token {token_string}"

    # Get default permissions or global fallbacks
    default_warehouse = frappe.db.get_value("User Permission", {"user": user.name, "allow": "Warehouse", "is_default": 1}, "for_value") or frappe.defaults.get_user_default("Warehouse") or "Stores - S"
    default_cost_center = frappe.db.get_value("User Permission", {"user": user.name, "allow": "Cost Center", "is_default": 1}, "for_value") or frappe.defaults.get_user_default("Cost Center") or "Main - S"
    default_customer = frappe.db.get_value("User Permission", {"user": user.name, "allow": "Customer", "is_default": 1}, "for_value") or "Cash Customer"
    default_company = frappe.db.get_value("User Permission", {"user": user.name, "allow": "Company", "is_default": 1}, "for_value") or frappe.defaults.get_user_default("Company") or "showline"

    user_rights_profile = None
    if getattr(user, "user_rights_profile", None):
        try:
            profile = frappe.get_doc("User Rights Profile", user.user_rights_profile)
            user_rights_profile = {
                "name": profile.name,
                "profile_name": profile.profile_name,
                "is_additional_tax_enabled": getattr(profile, "is_additional_tax_enabled", 0),
                "food_tax": getattr(profile, "food_tax", 0),
                "tourism_tax": getattr(profile, "tourism_tax", 0),
                "permissions": [
                    {
                        "feature": p.feature,
                        "can_read": p.can_read,
                        "can_create": p.can_create,
                        "can_update": p.can_update,
                        "can_delete": p.can_delete,
                        "can_submit": p.can_submit,
                    }
                    for p in getattr(profile, "permissions", [])
                ]
            }
        except Exception:
            user_rights_profile = None

    # Warehouse items
    warehouse_items = []
    if default_warehouse:
        try:
            warehouse_items = frappe.db.sql('''
                SELECT
                    item.item_code,
                    item.item_name,
                    item.description,
                    item.stock_uom,
                    bin.actual_qty,
                    bin.projected_qty
                FROM `tabItem` item
                LEFT JOIN `tabBin` bin ON bin.item_code = item.item_code AND bin.warehouse = %s
                WHERE item.disabled = 0
                LIMIT 50
            ''', default_warehouse, as_dict=1)
        except Exception:
            warehouse_items = []

    # Customers
    customers = []
    try:
        customers = frappe.get_list("Customer",
            fields=["name", "customer_name", "customer_group", "territory"],
            limit=50,
            ignore_permissions=True
        )
    except Exception:
        customers = []

    user_permissions = frappe.get_all(
        "User Permission",
        filters={"user": user.name},
        fields=["allow", "for_value"]
    )

    company_currency = frappe.db.get_value("Company", default_company, "default_currency") or "USD"

    frappe.response["message"] = "Logged In Successfully"
    frappe.response["auth_header"] = auth_header
    frappe.response["token_string"] = token_string
    frappe.response["token"] = base64.b64encode(token_string.encode("ascii")).decode("utf-8")
    frappe.response["api_key"] = api_generate["api_key"]
    frappe.response["api_secret"] = api_generate["api_secret"]
    frappe.response["user"] = {
        "first_name": frappe.utils.escape_html(user.first_name or ""),
        "last_name": frappe.utils.escape_html(user.last_name or ""),
        "full_name": user.full_name or "",
        "email": user.email or "",
        "warehouse": default_warehouse,
        "cost_center": default_cost_center,
        "company": default_company,
        "currency": company_currency,
        "default_customer": default_customer,
        "user_rights": user_rights_profile,
        "customers": customers,
        "warehouse_items": warehouse_items,
        "time_zone": {"client": local_tz, "server": erpnext_tz},
        "user_permissions": user_permissions
    }
    return


@frappe.whitelist()
def create_journal_entry(
    company=None,
    posting_date=None,
    voucher_type="Journal Entry",
    accounts=None,
    from_account=None,
    to_account=None,
    amount=None,
    cost_center=None,
    user_remark=None,
    cheque_no=None,
    cheque_date=None,
    docstatus=1
):
    import json
    import traceback

    if not company:
        company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("Company") or "showline"

    if not posting_date:
        posting_date = frappe.utils.today()

    if not cost_center:
        cost_center = frappe.defaults.get_user_default("Cost Center") or frappe.defaults.get_global_default("Cost Center") or "Main - S"

    je_accounts = []

    # Case 1: Full multi-account list provided
    if accounts:
        if isinstance(accounts, str):
            accounts = json.loads(accounts)
        for acc in accounts:
            if not acc.get("cost_center") and cost_center:
                acc["cost_center"] = cost_center
            je_accounts.append(acc)

    # Case 2: Simplified 2-legged entry (e.g. Expense / Cash transfer)
    elif from_account and to_account and amount:
        amt = float(amount)
        # Debit side (to_account, e.g. Expense)
        je_accounts.append({
            "account": to_account,
            "debit_in_account_currency": amt,
            "credit_in_account_currency": 0.0,
            "cost_center": cost_center
        })
        # Credit side (from_account, e.g. Cash / Bank)
        je_accounts.append({
            "account": from_account,
            "debit_in_account_currency": 0.0,
            "credit_in_account_currency": amt,
            "cost_center": cost_center
        })
    else:
        # Fallback to form_dict
        data = frappe.local.form_dict
        if "accounts" in data:
            raw_accs = data.get("accounts")
            if isinstance(raw_accs, str):
                raw_accs = json.loads(raw_accs)
            je_accounts = raw_accs

    if not je_accounts:
        frappe.throw("Account details or (from_account, to_account, amount) are required for Journal Entry.")

    try:
        je_dict = {
            "doctype": "Journal Entry",
            "voucher_type": voucher_type or "Journal Entry",
            "company": company,
            "posting_date": posting_date,
            "user_remark": user_remark or "POS / API Journal Entry",
            "accounts": je_accounts
        }
        if cheque_no:
            je_dict["cheque_no"] = cheque_no
        if cheque_date:
            je_dict["cheque_date"] = cheque_date

        je = frappe.get_doc(je_dict)
        je.insert()
        if int(docstatus or 1) == 1:
            je.submit()
        frappe.db.commit()
        return {"status": "success", "journal_entry": je.name, "name": je.name}
    except Exception as e:
        frappe.log_error(message=traceback.format_exc(), title="Create Journal Entry API Error")
        return {"status": "error", "message": str(e)}


def set_user_permission(user, doctype, value):
    """Utility to create or update User Permission"""
    if not value:
        return
    if not frappe.db.exists("User Permission", {"user": user, "allow": doctype, "for_value": value}):
        doc = frappe.get_doc({
            "doctype": "User Permission",
            "user": user,
            "allow": doctype,
            "for_value": value,
            "is_default": 1,
            "apply_to_all_doctypes": 1
        })
        doc.insert(ignore_permissions=True)

def set_defaults_for_user(user_email):
    try:
        default_company = frappe.db.get_single_value("Global Defaults", "default_company") or "showline"
        set_user_permission(user_email, "Company", default_company)

        main_cost_center = frappe.db.get_value(
            "Cost Center",
            {"company": default_company, "cost_center_name": ["like", "Main%"]},
            "name"
        )
        if main_cost_center:
            set_user_permission(user_email, "Cost Center", main_cost_center)

        main_warehouse = frappe.db.get_value(
            "Warehouse",
            {"company": default_company, "warehouse_name": ["like", "Stores%"]},
            "name"
        )
        if main_warehouse:
            set_user_permission(user_email, "Warehouse", main_warehouse)
    except Exception as e:
        frappe.log_error(f"Error in set_defaults_for_user: {e}")

def assign_initial_permissions(user_email):
    set_defaults_for_user(user_email)

def ensure_default_customer_for_user(user_email):
    try:
        if not frappe.db.exists("Customer", "Cash Customer"):
            frappe.get_doc({
                "doctype": "Customer",
                "customer_name": "Cash Customer",
                "customer_group": "Commercial",
                "territory": "Zimbabwe",
                "customer_type": "Individual"
            }).insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(f"Error ensuring customer: {e}")

@frappe.whitelist(allow_guest=True)
def create_user(email, password, first_name, last_name=None, full_name=None, pin=None, phone_number=None, role_profile=None):
    """
    API endpoint for user creation & signup copied from saas_api with defaults and role assignment
    """
    try:
        if not email or not password or not first_name:
            frappe.throw(frappe._("Email, password, and first name are required"))

        if frappe.db.exists("User", email):
            frappe.throw(frappe._("User with email {0} already exists").format(email))

        if not full_name:
            full_name = f"{first_name} {last_name}".strip() if last_name else first_name.strip()

        roles_to_assign = ["Stock User", "Accounts User", "Desk User"]
        existing_roles = frappe.get_all("Role", filters={"name": ["in", roles_to_assign]}, pluck="name")
        
        user_roles = [{"role": r} for r in existing_roles] if existing_roles else []

        user = frappe.get_doc({
            "doctype": "User",
            "email": email.strip(),
            "first_name": first_name.strip(),
            "last_name": (last_name or "").strip(),
            "full_name": full_name,
            "enabled": 1,
            "new_password": password,
            "send_welcome_email": 0,
            "user_type": "System User",
            "roles": user_roles
        })

        if phone_number:
            # Check if mobile_no already used
            if not frappe.db.exists("User", {"mobile_no": phone_number}):
                user.mobile_no = phone_number

        user.flags.ignore_permissions = True
        user.insert()

        # Set defaults and permissions
        setup_results = {"defaults": True, "permissions": True}
        try:
            set_defaults_for_user(email)
        except Exception:
            setup_results["defaults"] = False

        try:
            ensure_default_customer_for_user(email)
        except Exception:
            pass

        frappe.db.commit()

        return {
            "status": 200,
            "message": frappe._("User registered successfully"),
            "data": {
                "user": {
                    "email": email,
                    "full_name": full_name,
                    "first_name": first_name,
                    "last_name": last_name,
                    "roles": existing_roles,
                    "setup": setup_results
                }
            }
        }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Create User Error")
        frappe.response.http_status_code = 400
        return {
            "status": 400,
            "message": str(e)
        }


@frappe.whitelist()
def pull_all_stock_to_local(from_warehouse=None, company=None, device_id=None, target_warehouse=None, reset_cloud_stock=1):
    """
    Transfers/pulls all positive inventory from Cloud ERP warehouse into local POS terminal.
    If reset_cloud_stock is True (default 1):
      - Creates & submits a Stock Entry on Cloud ERP, draining the cloud warehouse stock to 0 (or transferring to target_warehouse).
    Returns complete product inventory payload for initializing local database.
    """
    try:
        company = company or frappe.db.get_single_value("Global Defaults", "default_company") or "showline"
        from_warehouse = from_warehouse or frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": ["like", "Stores%"]}, "name") or "Stores - S"
        reset_cloud_stock = frappe.parse_json(reset_cloud_stock) if isinstance(reset_cloud_stock, str) else bool(reset_cloud_stock)

        # 1. Fetch all items with positive stock in from_warehouse
        bins = frappe.db.sql("""
            SELECT b.item_code, b.warehouse, b.actual_qty, b.valuation_rate,
                   i.item_name, i.stock_uom, i.standard_rate, i.description, i.item_group
            FROM `tabBin` b
            JOIN `tabItem` i ON b.item_code = i.name
            WHERE b.warehouse = %(warehouse)s AND b.actual_qty > 0
        """, {"warehouse": from_warehouse}, as_dict=True)

        if not bins:
            # If no stock, return all items catalog with qty 0
            all_items = frappe.get_all("Item", fields=["name as item_code", "item_name", "stock_uom", "standard_rate", "item_group"])
            return {
                "status": 200,
                "message": "No active stock in cloud warehouse",
                "stock_entry": None,
                "total_items": len(all_items),
                "total_qty": 0.0,
                "from_warehouse": from_warehouse,
                "items": [{**item, "qty_transferred": 0.0} for item in all_items]
            }

        stock_items = []
        items_payload = []
        total_qty = 0.0

        for b in bins:
            qty = float(b.actual_qty)
            total_qty += qty
            rate = float(b.valuation_rate or b.standard_rate or 1.0)

            item_entry = {
                "item_code": b.item_code,
                "qty": qty,
                "s_warehouse": from_warehouse,
                "basic_rate": rate,
                "uom": b.stock_uom or "Nos",
                "stock_uom": b.stock_uom or "Nos",
                "conversion_factor": 1.0
            }

            if target_warehouse:
                item_entry["t_warehouse"] = target_warehouse

            stock_items.append(item_entry)

            items_payload.append({
                "item_code": b.item_code,
                "item_name": b.item_name,
                "item_group": b.item_group,
                "stock_uom": b.stock_uom,
                "standard_rate": float(b.standard_rate or rate),
                "valuation_rate": rate,
                "qty_transferred": qty,
                "description": b.description
            })

        stock_entry_name = None
        if reset_cloud_stock and stock_items:
            purpose = "Material Transfer" if target_warehouse else "Material Issue"
            stock_entry = frappe.get_doc({
                "doctype": "Stock Entry",
                "stock_entry_type": purpose,
                "purpose": purpose,
                "company": company,
                "from_warehouse": from_warehouse,
                "to_warehouse": target_warehouse if target_warehouse else None,
                "user_remark": f"Full Stock Pull to Local POS ({device_id or 'Local Terminal'})",
                "items": stock_items
            })
            stock_entry.set_stock_entry_type()
            stock_entry.flags.ignore_permissions = True
            stock_entry.insert()
            stock_entry.submit()
            frappe.db.commit()
            stock_entry_name = stock_entry.name

        return {
            "status": 200,
            "message": "Successfully transferred all stock from cloud to local app and reset cloud warehouse stock to 0" if reset_cloud_stock else "Successfully fetched stock snapshot",
            "stock_entry": stock_entry_name,
            "from_warehouse": from_warehouse,
            "target_warehouse": target_warehouse or "Local POS App",
            "total_items": len(items_payload),
            "total_qty": total_qty,
            "items": items_payload
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Pull All Stock Error")
        frappe.response.http_status_code = 400
        return {
            "status": 400,
            "message": str(e)
        }


@frappe.whitelist()
def get_products(page=1, limit=100, search_term=None, item_group=None, warehouse=None, stock_only=0):
    """
    Returns complete product catalog for POS terminals with barcodes, rates, and current warehouse stock.
    """
    try:
        page = int(page) if page else 1
        limit = int(limit) if limit else 100
        start = (page - 1) * limit

        default_company = frappe.db.get_single_value("Global Defaults", "default_company") or "showline"
        warehouse = warehouse or frappe.db.get_value("Warehouse", {"company": default_company, "warehouse_name": ["like", "Stores%"]}, "name") or "Stores - S"

        conditions = ["i.disabled = 0"]
        values = {"warehouse": warehouse, "start": start, "limit": limit}

        if item_group and item_group.strip():
            conditions.append("i.item_group = %(item_group)s")
            values["item_group"] = item_group.strip()

        if search_term and search_term.strip():
            conditions.append("(i.name LIKE %(search)s OR i.item_name LIKE %(search)s OR i.description LIKE %(search)s)")
            values["search"] = f"%{search_term.strip()}%"

        if stock_only and (str(stock_only) == "1" or stock_only is True):
            conditions.append("COALESCE(b.actual_qty, 0) > 0")

        where_clause = " AND ".join(conditions)

        base_from = "FROM `tabItem` i LEFT JOIN `tabBin` b ON b.item_code = i.name AND b.warehouse = %(warehouse)s"

        query = f"""
            SELECT 
                i.name AS item_code,
                i.item_name,
                i.item_group,
                i.stock_uom,
                i.standard_rate,
                i.description,
                i.image,
                COALESCE(b.actual_qty, 0) AS actual_qty,
                COALESCE(b.valuation_rate, i.standard_rate, 0) AS valuation_rate
            {base_from}
            WHERE {where_clause}
            ORDER BY i.item_name ASC
            LIMIT %(start)s, %(limit)s
        """

        items = frappe.db.sql(query, values, as_dict=True)

        # Get barcodes
        if items:
            item_codes = [it["item_code"] for it in items]
            barcodes = frappe.get_all("Item Barcode", filters={"parent": ["in", item_codes]}, fields=["parent", "barcode"])
            barcode_map = {}
            for bc in barcodes:
                barcode_map.setdefault(bc["parent"], []).append(bc["barcode"])

            for it in items:
                it["barcodes"] = barcode_map.get(it["item_code"], [])
                it["standard_rate"] = float(it["standard_rate"] or 0)
                it["actual_qty"] = float(it["actual_qty"] or 0)
                it["valuation_rate"] = float(it["valuation_rate"] or 0)

        total_count = frappe.db.sql(f"SELECT COUNT(*) {base_from} WHERE {where_clause}", values)[0][0]

        return {
            "status": 200,
            "message": "Products retrieved successfully",
            "page": page,
            "limit": limit,
            "total_count": total_count,
            "warehouse": warehouse,
            "data": items
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Products Error")
        frappe.response.http_status_code = 400
        return {"status": 400, "message": str(e)}


@frappe.whitelist()
def confirm_stock_receipt(stock_entry=None, device_id=None, received_items=None):
    """
    Validates and confirms two-way stock receipt between Cloud ERP and Local POS.
    Compares expected stock entry items with actual received quantities by terminal.
    """
    try:
        if isinstance(received_items, str):
            received_items = frappe.parse_json(received_items)

        if not received_items:
            frappe.throw(frappe._("received_items list is required for stock verification"))

        expected_items = {}
        if stock_entry:
            if not frappe.db.exists("Stock Entry", stock_entry):
                frappe.throw(frappe._("Stock Entry {0} not found").format(stock_entry))

            se_doc = frappe.get_doc("Stock Entry", stock_entry)
            for d in se_doc.items:
                expected_items[d.item_code] = float(d.qty or 0)

        discrepancies = []
        verified_count = 0

        for it in received_items:
            item_code = it.get("item_code")
            received_qty = float(it.get("received_qty", 0))

            expected_qty = expected_items.get(item_code, received_qty)
            variance = received_qty - expected_qty

            if variance != 0:
                discrepancies.append({
                    "item_code": item_code,
                    "expected_qty": expected_qty,
                    "received_qty": received_qty,
                    "variance": variance,
                    "status": "Shortage" if variance < 0 else "Excess"
                })
            else:
                verified_count += 1

        is_verified = len(discrepancies) == 0

        # Log confirmation on Stock Entry if provided
        if stock_entry:
            frappe.db.set_value(
                "Stock Entry",
                stock_entry,
                "user_remark",
                f"Receipt Verified by {device_id or 'Terminal'} | Match: {is_verified} | Discrepancies: {len(discrepancies)}"
            )
            frappe.db.commit()

        return {
            "status": 200,
            "verification_status": "VERIFIED_EXACT_MATCH" if is_verified else "DISCREPANCY_DETECTED",
            "stock_entry": stock_entry,
            "device_id": device_id or "POS-TERMINAL",
            "total_verified_items": verified_count,
            "total_discrepancies": len(discrepancies),
            "is_matched": is_verified,
            "discrepancies": discrepancies,
            "message": "Stock receipt verified: 100% exact match confirmed" if is_verified else f"Warning: {len(discrepancies)} inventory variances detected during receipt verification"
        }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Confirm Stock Receipt Error")
        frappe.response.http_status_code = 400
        return {"status": 400, "message": str(e)}



@frappe.whitelist()
def create_product(item_code, item_name, standard_rate=0.0, item_group="Products", stock_uom="Nos", barcode=None, opening_stock=0, stock_qty=0, warehouse=None, company=None, description=None):
    """
    Creates a new Product / Item in ERPNext with optional barcode and initial stock.
    """
    try:
        if not item_code or not item_name:
            frappe.throw(frappe._("Item Code and Item Name are required"))

        item_code = item_code.strip()
        if frappe.db.exists("Item", item_code):
            frappe.throw(frappe._("Item {0} already exists").format(item_code))

        company = company or frappe.db.get_single_value("Global Defaults", "default_company") or "showline"
        warehouse = warehouse or frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": ["like", "Stores%"]}, "name") or "Stores - S"
        standard_rate = float(standard_rate or 0)
        opening_stock = float(opening_stock or stock_qty or 0)

        # Check item group
        if not frappe.db.exists("Item Group", item_group):
            item_group = "Products" if frappe.db.exists("Item Group", "Products") else "All Item Groups"

        barcodes_list = []
        if barcode:
            barcodes_list.append({"barcode": barcode.strip()})

        item_doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_name.strip(),
            "item_group": item_group,
            "stock_uom": stock_uom or "Nos",
            "is_stock_item": 1,
            "standard_rate": standard_rate,
            "description": description or item_name,
            "barcodes": barcodes_list
        })
        item_doc.flags.ignore_permissions = True
        item_doc.insert()

        # If opening stock provided, create Stock Entry to initialize inventory
        stock_entry_name = None
        if opening_stock > 0 and warehouse:
            try:
                se = frappe.get_doc({
                    "doctype": "Stock Entry",
                    "stock_entry_type": "Material Receipt",
                    "purpose": "Material Receipt",
                    "company": company,
                    "to_warehouse": warehouse,
                    "user_remark": f"Opening stock for new item {item_code}",
                    "items": [{
                        "item_code": item_code,
                        "qty": opening_stock,
                        "t_warehouse": warehouse,
                        "basic_rate": standard_rate or 1.0,
                        "uom": stock_uom or "Nos",
                        "stock_uom": stock_uom or "Nos",
                        "conversion_factor": 1.0
                    }]
                })
                se.set_stock_entry_type()
                se.flags.ignore_permissions = True
                se.insert()
                se.submit()
                stock_entry_name = se.name
            except Exception as e:
                frappe.log_error(f"Error setting opening stock for {item_code}: {e}")

        frappe.db.commit()

        return {
            "status": 200,
            "message": f"Product {item_code} created successfully",
            "data": {
                "item_code": item_doc.name,
                "item_name": item_doc.item_name,
                "item_group": item_doc.item_group,
                "stock_uom": item_doc.stock_uom,
                "standard_rate": item_doc.standard_rate,
                "barcode": barcode,
                "opening_stock": opening_stock,
                "warehouse": warehouse if opening_stock > 0 else None,
                "stock_entry": stock_entry_name
            }
        }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Create Product Error")
        frappe.response.http_status_code = 400
        return {"status": 400, "message": str(e)}
