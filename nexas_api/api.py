import frappe
import base64
from tzlocal import get_localzone
import json


# Helpers
def generate_keys(user):
    api_secret = api_key = ''
    if not user.api_key and not user.api_secret:
        api_secret = frappe.generate_hash(length=15)
        # if api key is not set generate api key
        api_key = frappe.generate_hash(length=15)
        user.api_key = api_key
        user.api_secret = api_secret
        user.save(ignore_permissions=True)
    else:
        api_secret = user.get_password('api_secret')
        api_key = user.get('api_key')
    return {"api_secret": api_secret, "api_key": api_key}



@frappe.whitelist(allow_guest=True)
def create_customer(
    customer_name,
    customer_group,  # ✅ REQUIRED
    custom_trade_name=None,
    custom_customer_tin=None,
    custom_customer_vat=None,
    custom_customer_address=None,
    custom_telephone_number=None,
    custom_province=None,
    custom_street=None,
    custom_city=None,
    custom_house_no=None,
    custom_email_address=None,
    default_warehouse=None,
    customer_type="Individual",
    default_price_list=None,
    default_cost_center=None,
):
    try:
        # -------------------------------
        # VALIDATIONS
        # -------------------------------
        if not customer_group:
            frappe.throw("Customer Group is required.")

        if not frappe.db.exists("Customer Group", customer_group):
            frappe.throw(f"Invalid Customer Group: {customer_group}")

        if not default_price_list:
            frappe.throw("Price List is required for this customer.")

        if not default_cost_center:
            frappe.throw("Cost center is required for this customer.")

        if not default_warehouse:
            frappe.throw("Warehouse is required for this customer.")

        # -------------------------------
        # CREATE CUSTOMER
        # -------------------------------
        customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_group": customer_group,  # ✅ FIX
            "customer_type": customer_type,
            "custom_trade_name": custom_trade_name,
            "custom_customer_tin": custom_customer_tin,
            "custom_customer_vat": custom_customer_vat,
            "custom_customer_address": custom_customer_address,
            "custom_telephone_number": custom_telephone_number,
            "custom_province": custom_province,
            "custom_street": custom_street,
            "custom_city": custom_city,
            "custom_house_no": custom_house_no,
            "custom_email_address": custom_email_address,
            "custom_warehouse": default_warehouse,
            "custom_cost_center": default_cost_center,
            "default_price_list": default_price_list,
        })

        customer.insert(ignore_permissions=True)
        frappe.db.commit()

        return {
            "success": True,
            "customer_id": customer.name,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Create Customer Error")
        frappe.throw(str(e))


@frappe.whitelist()
def get_account():
    try:
        # Get logged-in user
        user = frappe.session.user

        # Try to get employee linked to user
        employee_company = frappe.db.get_value(
            "Employee",
            {"user_id": user},
            "company"
        )

        # Fallback: Use system default company
        if not employee_company:
            employee_company = frappe.db.get_single_value("Global Defaults", "default_company")

        # Fetch accounts for that company only
        accounts = frappe.get_all(
            "Account",
            filters={
                "company": employee_company,
                "account_type": ["in", ["Cash", "Bank"]],
                "is_group": 0
            },
            fields=[
                "name",
                "account_name",
                "account_number",
                "company",
                "parent_account",
                "account_type",
                "account_currency"
            ]
        )

        return {"status ": 200, "accounts":accounts}
        return

    except Exception as e:
        create_response("417", {"error": str(e)})
        frappe.log_error(message=str(e), title="Error fetching account data")
        return


@frappe.whitelist(allow_guest=True)
def login(usr, pwd, timezone):

    local_tz = str(get_localzone())
    erpnext_tz = frappe.utils.get_system_timezone()

    if timezone != erpnext_tz:
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

    # Get default permissions
    default_warehouse = frappe.db.get_value("User Permission", {"user": user.name, "allow": "Warehouse", "is_default": 1}, "for_value")
    default_cost_center = frappe.db.get_value("User Permission", {"user": user.name, "allow": "Cost Center", "is_default": 1}, "for_value")
    default_customer = frappe.db.get_value("User Permission", {"user": user.name, "allow": "Customer", "is_default": 1}, "for_value")
    default_company = frappe.db.get_value("User Permission", {"user": user.name, "allow": "Company", "is_default": 1}, "for_value")
    user_rights_profile = None
    DEFAULT_PROFILE_NAME = "Admin"
    if not user.user_rights_profile:
        if frappe.db.exists("User Rights Profile", DEFAULT_PROFILE_NAME):
            user.user_rights_profile = DEFAULT_PROFILE_NAME
            user.save(ignore_permissions=True)
            frappe.db.commit()
        else:
            frappe.log_error(
            f"Default User Rights Profile '{DEFAULT_PROFILE_NAME}' not found",
            "Login User Rights Profile"
        )
    if user.user_rights_profile:
        profile = frappe.get_doc("User Rights Profile", user.user_rights_profile)
        
        user_rights_profile = {
        "name": profile.name,
        "profile_name": profile.profile_name,
        "is_additional_tax_enabled": profile.is_additional_tax_enabled,
        "food_tax": profile.food_tax,
        "tourism_tax": profile.tourism_tax,
        "permissions": [
            {
                "feature": p.feature,
                "can_read": p.can_read,
                "can_create": p.can_create,
                "can_update": p.can_update,
                "can_delete": p.can_delete,
                "can_submit": p.can_submit,
            }
            for p in profile.permissions
            ]
        }

    # Warehouse items
    warehouse_items = []
    if default_warehouse:
        warehouse_items = frappe.db.sql("""
            SELECT 
                item.item_code,
                item.item_name,
                item.description,
                item.stock_uom,
                bin.actual_qty,
                bin.projected_qty
            FROM `tabItem` item
            LEFT JOIN `tabBin` bin ON bin.item_code = item.item_code 
            WHERE bin.warehouse = %s
        """, default_warehouse, as_dict=1)

    # Customers linked to cost center
    customers = []
    if default_cost_center:
        customers = frappe.get_list("Customer",
            filters={"custom_cost_center": default_cost_center},
            fields=["name", "customer_name", "customer_group", "territory", "custom_cost_center"],
            ignore_permissions=True
        )

    user_permissions = frappe.get_all(
        "User Permission",
        filters={"user": user.name},
        fields=["allow", "for_value"]
    )

    company_currency = None
    if default_company:
        company_currency = frappe.db.get_value("Company", default_company, "default_currency")
    if not company_currency:
        company_currency = frappe.defaults.get_global_default("default_currency") or "USD"

    frappe.response["user"] = {
        "first_name": escape_html(user.first_name or ""),
        "last_name": escape_html(user.last_name or ""),
        "gender": escape_html(user.gender or "") or "",
        "birth_date": user.birth_date or "",
        "mobile_no": user.mobile_no or "",
        "username": user.username or "",
        "full_name": user.full_name or "",
        "email": user.email or "",
        "warehouse": default_warehouse,
        "cost_center": default_cost_center,
        "company":default_company,
        "currency": company_currency,
        "default_customer": default_customer,
        "user_rights": user_rights_profile,
        "customers": customers,
        "warehouse_items": warehouse_items,
        "time_zone": {"client": local_tz, "server": erpnext_tz},
        "role": user.get("role_select"),
        "pin": user.get("pin"),
        "user_permissions": user_permissions
    }

    frappe.response["token_string"] = token_string
    frappe.response["token"] = base64.b64encode(token_string.encode("ascii")).decode("utf-8")
    return

# @frappe.whitelist()
# def create_sales_invoice():
#     invoice_data = frappe.local.form_dict
#     user = frappe.session.user

#     def get_user_default(fieldname):
#         value = invoice_data.get(fieldname)
#         if not value:
#             value = frappe.defaults.get_user_default(fieldname, user)
#         if not value:
#             value = frappe.db.get_value("User", user, fieldname)
#         if not value:
#             frappe.throw(f"{fieldname.replace('_',' ').title()} not provided and no default found for user")
#         return value

#     company = get_user_default("company")
#     customer = get_user_default("customer")
#     cost_center = get_user_default("cost_center")
#     warehouse = get_user_default("set_warehouse")
#     print(f"{company}, {customer}, {cost_center}, {warehouse}")

#     si_doc = frappe.get_doc({
#         "doctype": "Sales Invoice",
#         "customer": customer,
#         "company": company,
#         "currency": "USD",  # make sure this matches your default_currency or price lt
#         "conversion_rate": 1.0,  # if USD to USD, else fetch correct rate
#         "set_warehouse": warehouse,
#         "cost_center": cost_center,
#         "update_stock": invoice_data.get("update_stock", 1),
#         "posting_date": invoice_data.get("posting_date") or frappe.utils.nowdate(),
#         "posting_time": invoice_data.get("posting_time") or frappe.utils.nowtime(),
#         # "custom_sales_reference": invoice_data.get("custom_sales_reference"),
#         "taxes_and_charges": invoice_data.get("taxes_and_charges"),
#         "payments": invoice_data.get("payments", []),
#         "items": [
#             {
#                 "item_name": item.get("item_name"),
#                 "item_code": item.get("item_code"),
#                 "rate": item.get("rate"),
#                 "qty": item.get("qty"),
#                 "cost_center": item.get("cost_center") or cost_center
#             } for item in invoice_data.get("items", [])
#         ]
#     })


#     si_doc.insert()
#     a=si_doc.submit()
#     return a




@frappe.whitelist(allow_guest=False)
def create_invoice(customer=None, items=None, posting_date=None, posting_time=None, company=None, due_date=None,
                   cost_center=None,warehouse=None, update_stock=None, set_warehouse=None,
                   currency=None, reference_number=None):
    """
    Create a Sales Invoice with safe default handling.
    If required fields are not provided, it tries to fetch from user defaults.
    """
    if not items:
        frappe.throw("Invoice items must be provided.")
    if not reference_number:
        frappe.throw("reference_number is mandatory for creating an invoice.")
    import frappe
    if frappe.db.exists('Sales Invoice', {'po_no': reference_number}):
        frappe.throw(f"Sales Invoice with reference_number '{reference_number}' already exists.")

    # Convert items if passed as string
    if isinstance(items, str):
        items = json.loads(items)
    user = frappe.session.user
    print(f"user: {user}")
   
    def get_user_default(fieldname):
        """
        Loop through User Permissions and return the default value
        for the given fieldname for the current user.
        """
        user = frappe.session.user

        # Get all user permissions for this user
        perms = frappe.get_all(
            "User Permission",
            filters={"user": user, "allow": fieldname},
            fields=["for_value", "is_default"]
        )
        if not perms:
            frappe.throw(
                f"No user permissions found for '{fieldname}' for user {user}. "
                f"Please set a default in User Permissions."
            )

        # Find the one marked as default
        for p in perms:
            if p.get("is_default"):
                return p.get("for_value")

        # If none is default, just return the first one (optional)
        return perms[0].get("for_value")
    if not customer:
        customer = get_user_default("Customer")
    if not company:
        company = get_user_default("Company")
    if not cost_center:
       return "Cost Center is Mandatory"
    if not warehouse:
        warehouse = get_user_default("Warehouse")
    # Default posting/due dates
    if not posting_date:
        posting_date = frappe.utils.today()
    if not due_date:
        due_date = posting_date
    try:
        invoice_dict = {
            "doctype": "Sales Invoice",
            "customer": customer,
            "company": company,
            "posting_date": posting_date,
            "due_date": due_date,
            "cost_center": cost_center,
            "update_stock": update_stock,
            "set_warehouse": warehouse,
            "po_no": reference_number,
            "items": items
        }
        if posting_time:
            invoice_dict["posting_time"] = posting_time
            invoice_dict["set_posting_time"] = 1
            
        invoice = frappe.get_doc(invoice_dict)
        if currency:
            invoice.currency = currency
        invoice.insert()
        invoice.submit()
        frappe.db.commit()
        return {"status": "success", "invoice_name": invoice.name}
    except Exception as e:
        frappe.log_error(message=traceback.format_exc(), title="Create Invoice API Error")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def payment_entry(party, amount, mode_of_payment, reference_doctype=None, reference_name=None):
    if not party:
        frappe.throw("Party (customer) is required.")

    invoice = None

    # Use reference from payload if given
    if reference_doctype and reference_name:
        invoice = frappe.get_doc(reference_doctype, reference_name)

    # Otherwise, get last outstanding invoice
    if not invoice:
        last_invoice = frappe.get_list(
            "Sales Invoice",
            filters={"customer": party, "outstanding_amount": (">", 0)},
            fields=["name", "outstanding_amount"],
            order_by="posting_date desc, creation desc",
            limit=1
        )

        if last_invoice:
            invoice = frappe.get_doc("Sales Invoice", last_invoice[0].name)

    # Create Payment Entry
    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Receive"
    pe.party_type = "Customer"
    pe.party = party
    pe.paid_amount = amount
    pe.received_amount = amount
    pe.mode_of_payment = mode_of_payment

    # ⚡ Ignore permissions for everything below
    frappe.flags.ignore_permissions = True

    # Setup accounts etc.
    pe.setup_party_account_field()
    pe.set_missing_values()

    # Allocate to invoice if found
    if invoice:
        pe.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice.name,
            "allocated_amount": min(amount, invoice.outstanding_amount)
        })

    # Submit the Payment Entry
    pe.submit()

    # Reset ignore permissions
    frappe.flags.ignore_permissions = False

    return {
        "payment_entry": pe.name,
        "credited_invoice": invoice.name if invoice else None,
        "type": "Advance" if not invoice else "Invoice Payment"
    }


@frappe.whitelist()
def default_cost_center(company):
    """
    Returns the first Cost Center for a given company
    whose name starts with 'Main' (case-insensitive).
    """
    if not company:
        return None

    # Fetch all cost centers for this company
    cost_centers = frappe.get_all(
        "Cost Center",
        filters={"company": company},
        fields=["name"],
    )

    # Find the first one that starts with 'Main'
    for cc in cost_centers:
        cc_name = cc["name"]
        if cc_name.upper().startswith("MAIN"):
            print(f"-------------------Matched: {cc_name}")
            return cc_name

    return None

import json
from frappe import _
import frappe
from frappe.utils.data import get_datetime, flt
from datetime import timedelta


@frappe.whitelist()
def get_customers():
    try:
        # Get default cost center for the logged-in user
        default_cost_center = frappe.db.get_value(
            "User Permission", 
            {"user": frappe.session.user, "allow": "Cost Center", "is_default": 1}, 
            "for_value"
        )
        if not default_cost_center:
            return create_response("400", {"error": "No default Cost Center found for the user."})
        default_warehouse = frappe.db.get_value(
            "User Permission", 
            {"user": frappe.session.user, "allow": "Warehouse", "is_default": 1}, 
            "for_value"
        )
        if not default_warehouse:
            return create_response("400", {"error": "No default Warehouse found for the user."})
        # Fetch customers with default price list
        customers = frappe.get_all(
            "Customer",
            filters={
                "custom_cost_center": default_cost_center,
                "custom_warehouse": default_warehouse,
                "default_price_list": ["!=", ""]
            },
            fields=[
                "name",
                "customer_name",
                "customer_type",
                "custom_cost_center",
                "custom_warehouse",
                "gender",
                "customer_pos_id",
                "default_price_list"
            ]
        )
        for customer in customers:
            customer["balance"] = get_customer_balance(customer["name"])
            customer["items"] = frappe.get_all(
                "Item Price",
                filters={"price_list": customer["default_price_list"]},
                fields=["item_code", "item_name", "price_list_rate"]
            )


        create_response("200", customers)
        return

    except Exception as e:
        create_response("417", {"error": str(e)})
        frappe.log_error(message=str(e), title="Error fetching customer data")
        return


@frappe.whitelist()
def get_stock_reconciliation_with_items(from_date, to_date):
    reconciliations = frappe.get_all(
        "Stock Reconciliation",
        filters={
            "posting_date": ["between", [from_date, to_date]],
            "docstatus": 1
        },
        fields=[
            "name",
            "company",
            "posting_date",
            "purpose",
            "difference_amount",
            "cost_center"
        ]
    )

    if not reconciliations:
        return []

    names = [r.name for r in reconciliations]

    items = frappe.get_all(
        "Stock Reconciliation Item",
        filters={"parent": ["in", names]},
        fields=[
            "parent",
            "item_code",
            "warehouse",
            "qty",
            "current_qty",
            "quantity_difference",
            "valuation_rate",
            "amount",
            "amount_difference",
            "item_name"
        ]
    )

    items_map = {}
    for i in items:
        items_map.setdefault(i.parent, []).append(i)

    for r in reconciliations:
        r["items"] = items_map.get(r.name, [])

    return reconciliations


@frappe.whitelist()
def get_products():
    try:
        data = frappe.local.form_dict

        # Pagination
        page = int(data.get("page", 1))
        limit = int(data.get("limit", 1000))
        if page < 1:
            page = 1
        start = (page - 1) * limit

        item_group = data.get("item_group")

        filters = {"disabled": 0}

        user = frappe.session.user
        user_doc = frappe.get_doc("User", user)

        # --------------------------------------------------------
        # USER PERMISSIONS FOR ITEM GROUPS
        # --------------------------------------------------------
        allowed_item_groups = None

        if user_doc.user_rights_profile:
            profile = frappe.get_doc("User Rights Profile", user_doc.user_rights_profile)

            if profile.is_item_group_related:
                allowed_item_groups = frappe.get_all(
                    "User Permission",
                    filters={
                        "user": user_doc.name,
                        "allow": "Item Group"
                    },
                    fields=["for_value"]
                )

                allowed_item_groups = [g.for_value for g in allowed_item_groups]

                if not allowed_item_groups:
                    create_response("200", {
                        "products": [],
                        "pagination": {
                            "current_page": page,
                            "limit": limit,
                            "total_count": 0,
                            "total_pages": 0,
                            "has_next_page": False,
                            "has_prev_page": False,
                            "next_page": None,
                            "prev_page": None
                        }
                    })
                    return

                filters["item_group"] = ["in", allowed_item_groups]

        # --------------------------------------------------------
        # Optional API item_group filter
        # --------------------------------------------------------
        if item_group:
            if isinstance(item_group, list):
                if allowed_item_groups is not None:
                    intersection = list(set(item_group) & set(allowed_item_groups or []))
                    if not intersection:
                        create_response("200", {"products": [], "pagination": {}})
                        return
                    filters["item_group"] = ["in", intersection]
                else:
                    filters["item_group"] = ["in", item_group]
            else:
                if allowed_item_groups is not None and item_group not in allowed_item_groups:
                    create_response("200", {"products": [], "pagination": {}})
                    return
                filters["item_group"] = item_group

        # --------------------------------------------------------
        # Dynamic Item Fields (safe for missing columns)
        # --------------------------------------------------------
        item_fields = [
            "name",
            "item_name",
            "item_code",
            "item_group",
            "is_stock_item",
            "custom_simple_code",
            "is_sales_item",
            "stock_uom",
            # Variant fields — standard on the Item doctype.
            # `has_variants` is 1 on template items; `variant_of` holds the
            # parent template's item_code on variant children.
            "has_variants",
            "variant_of",
        ]

        has_food_tourism = frappe.db.has_column("Item", "custom_food_and_tourism_tax")
        has_food_tax = frappe.db.has_column("Item", "custom_food_tax")
        has_tourism_tax = frappe.db.has_column("Item", "custom_tourism_tax")
        cummulative = frappe.db.has_column("Item", "custom_cummulative")
        has_order_item_1 = frappe.db.has_column("Item", "custom_is_order_item_1")
        has_order_item_2 = frappe.db.has_column("Item", "custom_is_order_item_2")
        has_order_item_3 = frappe.db.has_column("Item", "custom_is_order_item_3")
        has_order_item_4 = frappe.db.has_column("Item", "custom_is_order_item_4")
        has_order_item_5 = frappe.db.has_column("Item", "custom_is_order_item_5")
        has_order_item_6 = frappe.db.has_column("Item", "custom_is_order_item_6")
        has_is_pharmacy_product = frappe.db.has_column("Item", "custom_is_pharmacy_product")

        if has_food_tourism:
            item_fields.append("custom_food_and_tourism_tax")
        if has_food_tax:
            item_fields.append("custom_food_tax")
        if has_tourism_tax:
            item_fields.append("custom_tourism_tax")
        if cummulative:
            item_fields.append("custom_cummulative")
        if has_order_item_1:
            item_fields.append("custom_is_order_item_1")
        if has_order_item_2:
            item_fields.append("custom_is_order_item_2")
        if has_order_item_3:
            item_fields.append("custom_is_order_item_3")
        if has_order_item_4:
            item_fields.append("custom_is_order_item_4")
        if has_order_item_5:
            item_fields.append("custom_is_order_item_5")
        if has_order_item_6:
            item_fields.append("custom_is_order_item_6")
        if has_is_pharmacy_product:
            item_fields.append("custom_is_pharmacy_product")

        # --------------------------------------------------------
        # Count
        # --------------------------------------------------------
        total_count = frappe.db.count("Item", filters=filters)

        # --------------------------------------------------------
        # Fetch Items
        # --------------------------------------------------------
        product_details = frappe.get_all(
            "Item",
            filters=filters,
            fields=item_fields,
            start=start,
            limit=limit,
            order_by="item_code"
        )

        # --------------------------------------------------------
        # UOM Conversions
        # --------------------------------------------------------
        uom_data = frappe.get_all(
            "UOM Conversion Detail",
            fields=["parent", "uom", "conversion_factor"]
        )

        uom_map = {}
        for u in uom_data:
            uom_map.setdefault(u["parent"], []).append({
                "uom": u["uom"],
                "conversion_factor": u["conversion_factor"]
            })

        # --------------------------------------------------------
        # Warehouses
        # --------------------------------------------------------
        bin_data = frappe.get_all(
            "Bin",
            fields=["item_code", "warehouse", "actual_qty"]
        )

        # --------------------------------------------------------
        # Prices
        # --------------------------------------------------------
        price_lists = frappe.get_all(
            "Item Price",
            fields=[
                "price_list",
                "price_list_rate",
                "item_code",
                "selling",
                "uom",
                "buying"
            ]
        )

        # --------------------------------------------------------
        # Batches (pharmacy / expiry tracking) - guarded
        # Never let batch fetch break the products endpoint.
        # --------------------------------------------------------
        batches_by_item = {}
        try:
            if frappe.db.table_exists("Batch"):
                item_codes = [p["item_code"] for p in product_details]
                if item_codes:
                    batch_filters = {
                        "item": ["in", item_codes],
                        "disabled": 0,
                    }
                    batch_fields = ["name", "batch_id", "item", "expiry_date"]
                    if frappe.db.has_column("Batch", "batch_qty"):
                        batch_fields.append("batch_qty")
                    batch_rows = frappe.db.get_all(
                        "Batch",
                        filters=batch_filters,
                        or_filters=[
                            ["expiry_date", "is", "not set"],
                            ["expiry_date", ">=", today()],
                        ],
                        fields=batch_fields,
                        limit_page_length=0,
                    )
                    for b in batch_rows:
                        batches_by_item.setdefault(b["item"], []).append({
                            "batch_no": b.get("batch_id") or b.get("name"),
                            "expiry_date": b.get("expiry_date"),
                            "qty": flt(b.get("batch_qty") or 0),
                        })
        except Exception:
            batches_by_item = {}

        # --------------------------------------------------------
        # Item Variant Attributes (bulk fetch for this page)
        # --------------------------------------------------------
        # `Item Variant Attribute` is the child table holding per-item
        # attribute values. We pull every row for the current page in one
        # go and group by `parent` (the item_code) — far cheaper than one
        # `frappe.get_doc` per item.
        attributes_by_item = {}
        try:
            page_codes = [p["item_code"] for p in product_details]
            if page_codes:
                attr_rows = frappe.get_all(
                    "Item Variant Attribute",
                    filters={"parent": ["in", page_codes]},
                    fields=["parent", "attribute", "attribute_value"],
                )
                for a in attr_rows:
                    attributes_by_item.setdefault(a["parent"], []).append({
                        "attribute":       a.get("attribute"),
                        "attribute_value": a.get("attribute_value"),
                    })
        except Exception:
            # Don't break get_products if the child table layout changes.
            attributes_by_item = {}

        products = {
            p["item_code"]: {
                "warehouses": [],
                "prices": [],
                "taxes": [],
                "barcodes":[]
            }
            for p in product_details
        }

        # Warehouses
        for b in bin_data:
            if b["item_code"] in products:
                products[b["item_code"]]["warehouses"].append({
                    "warehouse": b["warehouse"],
                    "qtyOnHand": b["actual_qty"]
                })

        for item_code, pdata in products.items():
            if not pdata["warehouses"]:
                pdata["warehouses"].append({
                    "warehouse": get_default_warehouse_for_user(),
                    "qtyOnHand": 0
                })

        # Prices
        for p in price_lists:
            if p["item_code"] in products:
                products[p["item_code"]]["prices"].append({
                    "priceName": p["price_list"],
                    "price": p["price_list_rate"],
                    "uom": p["uom"] or "nos",
                    "type": "selling" if p["selling"] else "buying"
                })

        # Taxes
        for p in product_details:
            item_code = p["item_code"]
            try:
                doc = frappe.get_doc("Item", item_code)
                for tax in getattr(doc, "taxes", []):
                    products[item_code]["taxes"].append({
                        "item_tax_template": tax.item_tax_template,
                        "tax_category": tax.tax_category,
                        "valid_from": tax.valid_from,
                        "minimum_net_rate": tax.minimum_net_rate,
                        "maximum_net_rate": tax.maximum_net_rate
                    })
            except Exception:
                pass
        
        # Barcode
        for p in product_details:
            item_code = p["item_code"]
            try:
                doc = frappe.get_doc("Item", item_code)
                for barcode in getattr(doc, "barcodes", []):
                    products[item_code]["barcodes"].append({
                        "barcode": barcode.barcode,
                        "barcode_type": barcode.barcode_type,
                        "uom": barcode.uom
                    })
            except Exception:
                pass
        # --------------------------------------------------------
        # Final Response
        # --------------------------------------------------------
        final_products = []

        for p in product_details:
            item_code = p["item_code"]

            product = {
                "itemcode": item_code,
                "itemname": p["item_name"],
                "groupname": p["item_group"],
                "maintainstock": p["is_stock_item"],
                "warehouses": products[item_code]["warehouses"],
                "default warehouse": get_default_warehouse_for_user(),
                "prices": products[item_code]["prices"],
                "barcodes": products[item_code]["barcodes"] if "barcodes" in products[item_code] else [],
                "taxes": products[item_code]["taxes"],
                "simple_code": p["custom_simple_code"],
                "is_sales_item": p["is_sales_item"],
                "test":"nothing",
                "uom": {
                    "stock_uom": p["stock_uom"],
                    "conversions": uom_map.get(item_code, [])
                },
                # Variant metadata (new in v2026.04):
                #   has_variants → 1 on template items (no price rows of
                #                  their own; variants carry them).
                #   variant_of   → parent template's item_code for variants.
                #   attributes   → list of {attribute, attribute_value} rows
                #                  from the Item Variant Attribute child
                #                  table. POS uses this to build the picker.
                "has_variants": bool(p.get("has_variants") or 0),
                "variant_of":   p.get("variant_of") or None,
                "attributes":   attributes_by_item.get(item_code, []),
            }

            if has_food_tourism:
                product["food_and_tourism_tax"] = p.get("custom_food_and_tourism_tax")

            if has_food_tax:
                product["food_tax"] = p.get("custom_food_tax")

            if has_tourism_tax:
                product["tourism_tax"] = p.get("custom_tourism_tax")
            if cummulative:
                product["cumulative"] = p.get("custom_cummulative")
            if has_order_item_1:
                product["custom_is_order_item_1"] = p.get("custom_is_order_item_1")
            if has_order_item_2:
                product["custom_is_order_item_2"] = p.get("custom_is_order_item_2")
            if has_order_item_3:
                product["custom_is_order_item_3"] = p.get("custom_is_order_item_3")
            if has_order_item_4:
                product["custom_is_order_item_4"] = p.get("custom_is_order_item_4")
            if has_order_item_5:
                product["custom_is_order_item_5"] = p.get("custom_is_order_item_5")
            if has_order_item_6:
                product["custom_is_order_item_6"] = p.get("custom_is_order_item_6")

            if has_is_pharmacy_product:
                product["is_pharmacy_product"] = bool(p.get("custom_is_pharmacy_product") or 0)
            else:
                product["is_pharmacy_product"] = False

            product["batches"] = batches_by_item.get(item_code, [])

            final_products.append(product)

        total_pages = (total_count + limit - 1) // limit

        pagination = {
            "current_page": page,
            "limit": limit,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next_page": page < total_pages,
            "has_prev_page": page > 1,
            "next_page": page + 1 if page < total_pages else None,
            "prev_page": page - 1 if page > 1 else None
        }

        create_response("200", {
            "products": final_products,
            "pagination": pagination
        })

    except Exception as e:
        create_response("417", {"error": str(e)})
        frappe.log_error(str(e), "Error fetching products")

# @frappe.whitelist()
# def get_products():
#     try:
#         data = frappe.local.form_dict

#         # Pagination
#         page = int(data.get("page", 1))
#         limit = int(data.get("limit", 1000))
#         if page < 1:
#             page = 1
#         start = (page - 1) * limit

#         item_group = data.get("item_group")

#         filters = {"disabled": 0}

#         user = frappe.session.user
#         user_doc = frappe.get_doc("User", user)

#         # --------------------------------------------------------
#         # 🔐 USER PERMISSIONS FOR ITEM GROUPS
#         # --------------------------------------------------------
#         allowed_item_groups = None

#         if user_doc.user_rights_profile:
#             profile = frappe.get_doc(
#                 "User Rights Profile",
#                 user_doc.user_rights_profile
#             )

#             if profile.is_item_group_related:
#                 # Fetch allowed item groups from User Permission table
#                 allowed_item_groups = frappe.get_all(
#                     "User Permission",
#                     filters={
#                         "user": user_doc.name,
#                         "allow": "Item Group"
#                     },
#                     fields=["for_value"]
#                 )
#                 allowed_item_groups = [g.for_value for g in allowed_item_groups]

#                 # If enabled but no groups assigned → return empty
#                 if not allowed_item_groups:
#                     create_response("200", {
#                         "products": [],
#                         "pagination": {
#                             "current_page": page,
#                             "limit": limit,
#                             "total_count": 0,
#                             "total_pages": 0,
#                             "has_next_page": False,
#                             "has_prev_page": False,
#                             "next_page": None,
#                             "prev_page": None
#                         }
#                     })
#                     return

#                 filters["item_group"] = ["in", allowed_item_groups]

#         # --------------------------------------------------------
#         # Optional API item_group filter (intersect safely)
#         # --------------------------------------------------------
#         if item_group:
#             if isinstance(item_group, list):
#                 if allowed_item_groups is not None:
#                     intersection = list(
#                         set(item_group) & set(allowed_item_groups or [])
#                     )
#                     if not intersection:
#                         create_response("200", {
#                             "products": [],
#                             "pagination": {
#                                 "current_page": page,
#                                 "limit": limit,
#                                 "total_count": 0,
#                                 "total_pages": 0,
#                                 "has_next_page": False,
#                                 "has_prev_page": False,
#                                 "next_page": None,
#                                 "prev_page": None
#                             }
#                         })
#                         return
#                     filters["item_group"] = ["in", intersection]
#                 else:
#                     filters["item_group"] = ["in", item_group]
#             else:
#                 if allowed_item_groups is not None:
#                     if item_group not in allowed_item_groups:
#                         create_response("200", {
#                             "products": [],
#                             "pagination": {
#                                 "current_page": page,
#                                 "limit": limit,
#                                 "total_count": 0,
#                                 "total_pages": 0,
#                                 "has_next_page": False,
#                                 "has_prev_page": False,
#                                 "next_page": None,
#                                 "prev_page": None
#                             }
#                         })
#                         return
#                 filters["item_group"] = item_group

#         # --------------------------------------------------------
#         # Count
#         # --------------------------------------------------------
#         total_count = frappe.db.count("Item", filters=filters)

#         # --------------------------------------------------------
#         # Fetch Items
#         # --------------------------------------------------------
#         product_details = frappe.get_all(
#             "Item",
#             filters=filters,
#             fields=[
#                 "name",
#                 "item_name",
#                 "item_code",
#                 "item_group",
#                 "is_stock_item",
#                 "custom_simple_code",
#                 "is_sales_item",
#                 "stock_uom",
#                 "custom_food_and_tourism_tax",
#                 "custom_food_tax",
#                  "custom_tourism_tax"
#             ],
#             start=start,
#             limit=limit,
#             order_by="item_code"
#         )

#         # --------------------------------------------------------
#         # UOM Conversions
#         # --------------------------------------------------------
#         uom_data = frappe.get_all(
#             "UOM Conversion Detail",
#             fields=["parent", "uom", "conversion_factor"]
#         )

#         uom_map = {}
#         for u in uom_data:
#             uom_map.setdefault(u["parent"], []).append({
#                 "uom": u["uom"],
#                 "conversion_factor": u["conversion_factor"]
#             })

#         # --------------------------------------------------------
#         # Warehouses
#         # --------------------------------------------------------
#         bin_data = frappe.get_all(
#             "Bin",
#             fields=["item_code", "warehouse", "actual_qty"]
#         )

#         # --------------------------------------------------------
#         # Prices
#         # --------------------------------------------------------
#         price_lists = frappe.get_all(
#             "Item Price",
#             fields=[
#                 "price_list",
#                 "price_list_rate",
#                 "item_code",
#                 "selling",
#                 "uom",
#                 "buying"
#             ]
#         )

#         products = {
#             p["item_code"]: {
#                 "warehouses": [],
#                 "prices": [],
#                 "taxes": []
#             }
#             for p in product_details
#         }

#         # Warehouse qty
#         for b in bin_data:
#             if b["item_code"] in products:
#                 products[b["item_code"]]["warehouses"].append({
#                     "warehouse": b["warehouse"],
#                     "qtyOnHand": b["actual_qty"]
#                 })

#         for item_code, pdata in products.items():
#             if not pdata["warehouses"]:
#                 pdata["warehouses"].append({
#                     "warehouse": get_default_warehouse_for_user(),
#                     "qtyOnHand": 0
#                 })

#         # Prices
#         for p in price_lists:
#             if p["item_code"] in products:
#                 products[p["item_code"]]["prices"].append({
#                     "priceName": p["price_list"],
#                     "price": p["price_list_rate"],
#                     "uom": p["uom"] or "nos",
#                     "type": "selling" if p["selling"] else "buying"
#                 })

#         # Taxes
#         for p in product_details:
#             item_code = p["item_code"]
#             try:
#                 doc = frappe.get_doc("Item", item_code)
#                 for tax in getattr(doc, "taxes", []):
#                     products[item_code]["taxes"].append({
#                         "item_tax_template": tax.item_tax_template,
#                         "tax_category": tax.tax_category,
#                         "valid_from": tax.valid_from,
#                         "minimum_net_rate": tax.minimum_net_rate,
#                         "maximum_net_rate": tax.maximum_net_rate
#                     })
#             except Exception:
#                 pass

#         # --------------------------------------------------------
#         # Final Response
#         # --------------------------------------------------------
#         final_products = []

#         for p in product_details:
#             item_code = p["item_code"]
#             final_products.append({
#                 "itemcode": item_code,
#                 "itemname": p["item_name"],
#                 "groupname": p["item_group"],
#                 "maintainstock": p["is_stock_item"],
#                 "food_and_tourism_tax": p["custom_food_and_tourism_tax"],
#                 "food_tax": p["custom_food_tax"],
#                 "tourism_tax": p["custom_tourism_tax"],
#                 "warehouses": products[item_code]["warehouses"],
#                 "default warehouse": get_default_warehouse_for_user(),
#                 "prices": products[item_code]["prices"],
#                 "taxes": products[item_code]["taxes"],
#                 "simple_code": p["custom_simple_code"],
#                 "is_sales_item": p["is_sales_item"],
#                 "uom": {
#                     "stock_uom": p["stock_uom"],
#                     "conversions": uom_map.get(item_code, [])
#                 },
#             })

#         total_pages = (total_count + limit - 1) // limit

#         pagination = {
#             "current_page": page,
#             "limit": limit,
#             "total_count": total_count,
#             "total_pages": total_pages,
#             "has_next_page": page < total_pages,
#             "has_prev_page": page > 1,
#             "next_page": page + 1 if page < total_pages else None,
#             "prev_page": page - 1 if page > 1 else None
#         }

#         create_response("200", {
#             "products": final_products,
#             "pagination": pagination
#         })

#     except Exception as e:
#         create_response("417", {"error": str(e)})
#         frappe.log_error(str(e), "Error fetching products")




@frappe.whitelist()
def create_journal_entry():
    data = frappe.local.form_dict
    
    je = frappe.new_doc('Journal Entry')
    je.voucher_type = data.get('voucher_type', 'Journal Entry')
    je.posting_date = data.get('posting_date', frappe.utils.nowdate())
    je.company = data.get('company')
    
    if data.get('accounts'):
        import json
        accounts = json.loads(data.get('accounts'))
        for acc in accounts:
            je.append('accounts', {
                'account': acc.get('account'),
                'debit_in_account_currency': acc.get('debit', 0.0),
                'credit_in_account_currency': acc.get('credit', 0.0),
                'cost_center': acc.get('cost_center')
            })
            
    je.insert(ignore_permissions=True)
    je.submit()
    
    return {'status': 'success', 'name': je.name}


def get_default_warehouse_for_user():
    """
    Returns the default warehouse assigned to the logged-in user via User Permission.
    If none found, returns None.
    """
    try:
        user = frappe.session.user  # get the logged-in user
        if user == "Guest":
            return None

        warehouse_permission = frappe.get_all(
            "User Permission",
            filters={
                "user": user,
                "allow": "Warehouse",
                "is_default": 1
            },
            fields=["for_value"],
            limit=1
        )

        if warehouse_permission:
            return warehouse_permission[0]["for_value"]

    except Exception as e:
        frappe.log_error(e, "get_default_warehouse_for_user")

    return None




def create_response(status, data):
    frappe.response['status'] = status
    for k, v in data.items():
        frappe.response[k] = v
    return

def create_test_data():
    import frappe
    # Create Item Group if not exists
    if not frappe.db.exists('Item Group', 'Test POS Group'):
        frappe.get_doc({
            'doctype': 'Item Group',
            'item_group_name': 'Test POS Group',
            'parent_item_group': 'All Item Groups',
            'is_group': 0
        }).insert(ignore_permissions=True)

    # Create Items
    items = [
        {'item_code': 'TEST-POS-001', 'item_name': 'Test Burger', 'standard_rate': 5.0, 'item_group': 'Test POS Group'},
        {'item_code': 'TEST-POS-002', 'item_name': 'Test Cola', 'standard_rate': 2.0, 'item_group': 'Test POS Group'}
    ]

    for item in items:
        if not frappe.db.exists('Item', item['item_code']):
            frappe.get_doc({
                'doctype': 'Item',
                'item_code': item['item_code'],
                'item_name': item['item_name'],
                'item_group': item['item_group'],
                'is_stock_item': 1,
                'stock_uom': 'Nos',
                'valuation_rate': item['standard_rate']
            }).insert(ignore_permissions=True)

            if not frappe.db.exists('Item Price', {'item_code': item['item_code'], 'price_list': 'Standard Selling'}):
                frappe.get_doc({
                    'doctype': 'Item Price',
                    'price_list': 'Standard Selling',
                    'item_code': item['item_code'],
                    'price_list_rate': item['standard_rate']
                }).insert(ignore_permissions=True)

    # Create Customer
    if not frappe.db.exists('Customer', 'Test POS Customer'):
        frappe.get_doc({
            'doctype': 'Customer',
            'customer_name': 'Test POS Customer',
            'customer_group': 'Commercial',
            'territory': 'All Territories',
            'customer_type': 'Company'
        }).insert(ignore_permissions=True)

    frappe.db.commit()
    return 'Test data created successfully!'

def test_all_endpoints():
    import json
    import frappe
    import traceback
    results = {}
    
    frappe.set_user('Administrator')

    # 1. get_products
    try:
        frappe.form_dict = frappe._dict({'page': 1, 'limit': 10})
        frappe.response = frappe._dict()
        get_products()
        results['get_products'] = 'Pass'
    except Exception as e:
        results['get_products'] = f'Fail: {str(e)}'

    # 2. create_invoice
    try:
        frappe.response = frappe._dict()
        res = create_invoice(
            customer='Test POS Customer', 
            items=json.dumps([{'item_code': 'TEST-POS-001', 'qty': 1, 'rate': 100}]), 
            reference_number='POS-TEST-002',
            posting_time='12:00:00'
        )
        if res.get('status') == 'error':
            results['create_invoice'] = f"Fail: {res.get('message')}"
        else:
            results['create_invoice'] = 'Pass'
    except Exception as e:
        results['create_invoice'] = f'Fail: {str(e)}\n{traceback.format_exc()}'

    # 3. payment_entry
    try:
        frappe.response = frappe._dict()
        payment_entry(
            party='Test POS Customer',
            amount=100,
            mode_of_payment='Cash'
        )
        results['payment_entry'] = 'Pass'
    except Exception as e:
        results['payment_entry'] = f'Fail: {str(e)}'

    # 4. default_cost_center
    try:
        frappe.response = frappe._dict()
        frappe.form_dict = frappe._dict({'company': 'Showline'})
        default_cost_center()
        results['default_cost_center'] = 'Pass'
    except Exception as e:
        results['default_cost_center'] = f'Fail: {str(e)}'

    # 5. get_account
    try:
        frappe.response = frappe._dict()
        get_account()
        results['get_account'] = 'Pass'
    except Exception as e:
        results['get_account'] = f'Fail: {str(e)}'

    # 6. get_stock_reconciliation_with_items
    try:
        frappe.response = frappe._dict()
        frappe.form_dict = frappe._dict({'from_date': '2026-01-01', 'to_date': '2026-12-31'})
        get_stock_reconciliation_with_items()
        results['get_stock_reconciliation_with_items'] = 'Pass'
    except Exception as e:
        results['get_stock_reconciliation_with_items'] = f'Fail: {str(e)}'

    # 7. get_customers
    try:
        frappe.response = frappe._dict()
        get_customers()
        results['get_customers'] = 'Pass'
    except Exception as e:
        results['get_customers'] = f'Fail: {str(e)}'

    # 8. create_customer
    try:
        frappe.response = frappe._dict()
        frappe.form_dict = frappe._dict({
            'customer_name': 'Test API Customer',
            'customer_group': 'Commercial',
            'territory': 'All Territories'
        })
        create_customer()
        results['create_customer'] = 'Pass'
    except Exception as e:
        results['create_customer'] = f'Fail: {str(e)}'

    # 9. create_journal_entry
    try:
        frappe.response = frappe._dict()
        frappe.form_dict = frappe._dict({
            'company': 'Showline',
            'accounts': json.dumps([
                {'account': 'Cash - S', 'debit': 100, 'cost_center': 'Main - S'},
                {'account': 'Sales - S', 'credit': 100, 'cost_center': 'Main - S'}
            ])
        })
        create_journal_entry()
        results['create_journal_entry'] = 'Pass'
    except Exception as e:
        results['create_journal_entry'] = f'Fail: {str(e)}'

    for k, v in results.items():
        print(f"{k}: {v}")
