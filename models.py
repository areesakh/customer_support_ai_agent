from datetime import datetime
from db import db
from flask_login import UserMixin

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    domain = db.Column(db.String(100), nullable=False)
    meal_allowance_refresh_cycle = db.Column(db.String(20), default='monthly')
    default_meal_allowance = db.Column(db.Float, default=0.0)

class Cart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship('Product', backref='cart_items')

    @property
    def subtotal(self):
        return self.product.price * self.quantity

class Employee(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(128))
    meal_allowance = db.Column(db.Float, default=0)
    credit_balance = db.Column(db.Float, default=0)
    credit_card = db.Column(db.JSON)
    cart_items = db.relationship('Cart', backref='employee', lazy=True)
    is_active = db.Column(db.Boolean, default=True)
    failed_login_attempts = db.Column(db.Integer, default=0)
    last_failed_login = db.Column(db.DateTime)
    account_locked_until = db.Column(db.DateTime)
    password_reset_token = db.Column(db.String(100), unique=True)
    password_reset_expires = db.Column(db.DateTime)

    def __init__(self, email, name, password, meal_allowance=0, credit_balance=0, credit_card=None):
        self.email = email
        self.name = name
        self.meal_allowance = meal_allowance
        self.credit_balance = credit_balance
        self.credit_card = credit_card or {}
        if password:
            self.set_password(password)

    def calculate_payment_breakdown(self, total_amount):
        """Calculate how much to charge from meal allowance vs credit balance vs credit card"""
        remaining = total_amount
        meal_allowance_used = min(remaining, self.meal_allowance)
        remaining -= meal_allowance_used

        credit_balance_used = min(remaining, self.credit_balance)
        remaining -= credit_balance_used

        return {
            'meal_allowance': meal_allowance_used,
            'credit_balance': credit_balance_used,
            'credit_card': remaining
        }

    def set_password(self, password):
        """Set password hash for user"""
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Check if password matches hash"""
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)

    def is_account_locked(self):
        """Check if account is currently locked"""
        if not self.account_locked_until:
            return False
        return datetime.utcnow() < self.account_locked_until

    def increment_failed_login(self):
        """Increment failed login attempts and lock account if needed"""
        self.failed_login_attempts += 1
        self.last_failed_login = datetime.utcnow()
        
        # Lock account for increasing durations based on number of attempts
        if self.failed_login_attempts >= 5:
            lock_duration = timedelta(minutes=2 ** (self.failed_login_attempts - 4))  # 2, 4, 8, 16... minutes
            self.account_locked_until = datetime.utcnow() + lock_duration

    def reset_failed_login(self):
        """Reset failed login attempts after successful login"""
        self.failed_login_attempts = 0
        self.last_failed_login = None
        self.account_locked_until = None

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=True)
    dietary_info = db.Column(db.Text, nullable=True)
    available = db.Column(db.Boolean, default=True)

class SupportTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_email = db.Column(db.String(120), nullable=False)
    issue = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='open')  # open, in_progress, resolved, closed
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    ticket_type = db.Column(db.String(50), nullable=False, default='general')  # general, cancellation, refund, etc.
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'customer_email': self.customer_email,
            'issue': self.issue,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'ticket_type': self.ticket_type,
            'order_id': self.order_id
        }

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    order_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    allowance_used = db.Column(db.Float, default=0)
    credits_used = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), nullable=False, default='order_received')
    estimated_delivery = db.Column(db.DateTime)
    customer_email = db.Column(db.String(120))
    product = db.relationship('Product', backref='orders')
    employee = db.relationship('Employee', backref='orders')

    ORDER_STATUSES = [
        'order_received',
        'order_confirmed',
        'preparing_order',
        'waiting_for_driver',
        'order_on_the_way',
        'order_delivered',
        'cancelled',
        'cancellation_requested'
    ]

    def get_status_display(self):
        return self.status.replace('_', ' ').title()

    def to_dict(self):
        return {
            'id': self.id,
            'product_name': self.product.name,
            'quantity': self.quantity,
            'total_price': self.price * self.quantity,
            'order_time': self.order_time.isoformat(),
            'status': self.status,  # Return raw status for frontend comparison
            'status_display': self.get_status_display(),  # Return formatted status for display
            'estimated_delivery': self.estimated_delivery.isoformat() if self.estimated_delivery else None,
            'payment_breakdown': {
                'meal_allowance': self.allowance_used,
                'credit_balance': self.credits_used,
                'card_charged': (self.price * self.quantity) - self.allowance_used - self.credits_used
            }
        }

class Refund(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    credit_amount = db.Column(db.Float, default=0.0)  # Amount given as credit instead of refund
