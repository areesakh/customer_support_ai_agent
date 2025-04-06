from flask import Flask, render_template, request, jsonify, session, url_for, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv, dotenv_values
import json
from itsdangerous import URLSafeTimedSerializer
import uuid
from functools import wraps

# Read API key directly from .env file
env_path = os.path.join(os.path.dirname(__file__), '.env')
api_key = None

try:
    with open(env_path, 'r') as f:
        for line in f:
            if line.startswith('OPENAI_API_KEY='):
                api_key = line.split('=', 1)[1].strip()
                break
    
    if api_key:
        print(f"Found API key in .env, length: {len(api_key)}")
        print(f"First 4 chars: {api_key[:4]}")
        print(f"Last 4 chars: {api_key[-4:]}")
        # Set it in environment
        os.environ['OPENAI_API_KEY'] = api_key
    else:
        print("Could not find OPENAI_API_KEY in .env file")
        
except Exception as e:
    print(f"Error reading .env file: {str(e)}")

from flask import Flask, jsonify, request, session, url_for

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-key-123'  # Use a fixed key for development
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shop.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = False  # Allow non-HTTPS for development
app.config['SECURITY_PASSWORD_SALT'] = 'email-confirm-key'

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return Employee.query.get(int(user_id))

def generate_token(email):
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    return serializer.dumps(email, salt=app.config['SECURITY_PASSWORD_SALT'])

def confirm_token(token, expiration=3600):
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        email = serializer.loads(
            token,
            salt=app.config['SECURITY_PASSWORD_SALT'],
            max_age=expiration
        )
        return email
    except:
        return False

# Database Models
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    domain = db.Column(db.String(100), nullable=False)
    meal_allowance_refresh_cycle = db.Column(db.String(20), default='monthly')  # monthly, weekly, daily
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
        return self.quantity * self.product.price

class Employee(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(128))
    meal_allowance = db.Column(db.Float, default=0)
    credit_balance = db.Column(db.Float, default=0)
    credit_card = db.Column(db.JSON)
    
    # Relationships
    cart_items = db.relationship('Cart', backref='employee', lazy=True)
    
    def __init__(self, email, name, password, meal_allowance=0, credit_balance=0, credit_card=None):
        self.email = email
        self.name = name
        self.meal_allowance = meal_allowance
        self.credit_balance = credit_balance
        self.credit_card = credit_card or {}
        if password:
            self.set_password(password)
    
    def calculate_payment_breakdown(self, total_amount):
        remaining = total_amount
        
        # Use allowance first
        allowance_used = min(self.meal_allowance, remaining)
        remaining -= allowance_used
        
        # Use credits next
        credits_used = min(self.credit_balance, remaining)
        remaining -= credits_used
        
        # Calculate tax (10%)
        tax = total_amount * 0.1
        
        return {
            'subtotal': total_amount,
            'tax': tax,
            'grand_total': total_amount + tax,
            'allowance_used': allowance_used,
            'credits_used': credits_used,
            'credit_card_charge': remaining
        }
    is_active = db.Column(db.Boolean, default=True)
    failed_login_attempts = db.Column(db.Integer, default=0)
    last_failed_login = db.Column(db.DateTime)
    account_locked_until = db.Column(db.DateTime)
    password_reset_token = db.Column(db.String(100), unique=True)
    password_reset_expires = db.Column(db.DateTime)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def is_account_locked(self):
        if self.account_locked_until and self.account_locked_until > datetime.utcnow():
            return True
        return False
    
    def increment_failed_login(self):
        self.failed_login_attempts += 1
        self.last_failed_login = datetime.utcnow()
        
        if self.failed_login_attempts >= 3:
            self.account_locked_until = datetime.utcnow() + timedelta(minutes=30)
        
        db.session.commit()
    
    def reset_failed_login(self):
        self.failed_login_attempts = 0
        self.last_failed_login = None
        self.account_locked_until = None
        db.session.commit()

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=True)
    dietary_info = db.Column(db.Text, nullable=True)  # JSON: allergens, dietary restrictions
    available = db.Column(db.Boolean, default=True)

class SupportTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_email = db.Column(db.String(120), nullable=False)
    issue = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='open')  # open, in_progress, resolved
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    ticket_type = db.Column(db.String(50), nullable=False, default='general')  # general, cancellation
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'customer_email': self.customer_email,
            'issue': self.issue,
            'status': self.status,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
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
        'order_delivered'
    ]
    
    def get_status_display(self):
        return self.status.replace('_', ' ').title()
    
    def to_dict(self):
        return {
            'id': self.id,
            'product': {
                'name': self.product.name,
                'price': self.price
            },
            'quantity': self.quantity,
            'total': self.price * self.quantity,
            'order_time': self.order_time.isoformat(),
            'estimated_delivery': self.estimated_delivery.isoformat() if self.estimated_delivery else None,
            'status': self.status,
            'status_display': self.get_status_display(),
            'payment': {
                'allowance_used': self.allowance_used,
                'credits_used': self.credits_used,
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

# Ensure the instance folder exists
if not os.path.exists('instance'):
    os.makedirs('instance')

# Initialize RAG engines for different sessions
from rag_engine import RAGEngine
from uuid import uuid4

rag_engines = {}

def get_or_create_rag_engine(session_id):
    if session_id not in rag_engines:
        rag_engines[session_id] = RAGEngine('support_sop.txt', api_key=api_key)
    return rag_engines[session_id]

def get_ai_response(message, session_id, customer_email=None):
    try:
        # Get or create RAG engine for this session
        engine = get_or_create_rag_engine(session_id)
        
        # Get AI response using RAG with conversation history and customer context
        response = engine.generate_response(message, customer_email=customer_email)
        return response
    except Exception as e:
        print(f"Error in get_ai_response: {str(e)}")
        return f"I apologize, but I'm having trouble processing your request. Please try again later."

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/order-tracking/<int:order_id>')
@login_required
def order_tracking(order_id):
    order = Order.query.get_or_404(order_id)
    if order.employee_id != current_user.id:
        abort(403)
    return render_template('order_tracking.html', order=order)

@app.route('/order-history')
@login_required
def order_history():
    orders = Order.query.filter_by(employee_id=current_user.id)\
                       .order_by(Order.order_time.desc())\
                       .all()
    return render_template('order_history.html', orders=orders)

@app.route('/api/orders/<int:order_id>')
@login_required
def get_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.employee_id != current_user.id:
        abort(403)
    return jsonify(order.to_dict())

@app.route('/api/orders/history')
@login_required
def get_order_history():
    orders = Order.query.filter_by(employee_id=current_user.id)\
                       .order_by(Order.order_time.desc())\
                       .all()
    return jsonify([order.to_dict() for order in orders])

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        if not data:
            print("No JSON data received")
            return jsonify({'error': 'No data provided'}), 400
            
        email = data.get('email')
        password = data.get('password')
        
        print(f"Login attempt for email: {email}")
        
        if not email or not password:
            print("Missing email or password")
            return jsonify({'error': 'Email and password are required'}), 400
        
        # Ensure we're in an app context
        with app.app_context():
            employee = Employee.query.filter_by(email=email).first()
            if not employee:
                print(f"No employee found with email: {email}")
                return jsonify({'error': 'Invalid email or password'}), 401
            
            print(f"Found employee: {employee.email}")
            print(f"Password hash exists: {'Yes' if employee.password_hash else 'No'}")
            
            # Check password
            if not employee.check_password(password):
                print(f"Password check failed for {email}")
                return jsonify({'error': 'Invalid email or password'}), 401
            
            print(f"Password check successful for {email}")
            
            # Log in the user
            login_user(employee)
            session['customer_email'] = email
            
            return jsonify({
                'message': 'Login successful',
                'email': email,
                'id': employee.id
            })
            
    except Exception as e:
        print(f"Error during login: {str(e)}")
        return jsonify({'error': 'An error occurred during login'}), 500

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    session.pop('customer_email', None)
    session.pop('session_id', None)
    return jsonify({'message': 'Logout successful'})


@app.route('/request-password-reset', methods=['POST'])
def request_password_reset():
    data = request.json
    email = data.get('email')
    
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    
    employee = Employee.query.filter_by(email=email).first()
    if not employee:
        # Don't reveal that the email doesn't exist
        return jsonify({'message': 'If the email exists, a reset link will be sent'})
    
    # Generate password reset token
    token = generate_token(email)
    employee.password_reset_token = token
    employee.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
    db.session.commit()
    
    # In a real application, send this via email
    reset_url = url_for('reset_password', token=token, _external=True)
    
    return jsonify({
        'message': 'Password reset instructions sent',
        'reset_url': reset_url  # Only for development
    })

@app.route('/reset-password/<token>', methods=['POST'])
def reset_password(token):
    data = request.json
    new_password = data.get('new_password')
    
    if not new_password:
        return jsonify({'error': 'New password is required'}), 400
    
    employee = Employee.query.filter_by(password_reset_token=token).first()
    if not employee or not employee.password_reset_expires or \
       employee.password_reset_expires < datetime.utcnow():
        return jsonify({'error': 'Invalid or expired reset token'}), 400
    
    # Reset password
    employee.set_password(new_password)
    employee.password_reset_token = None
    employee.password_reset_expires = None
    employee.reset_failed_login()
    db.session.commit()
    
    return jsonify({'message': 'Password reset successful'})

@app.route('/api/set_email', methods=['POST'])
def set_email():
    data = request.json
    email = data.get('email')
    if email:
        session['customer_email'] = email
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Email is required'})

@app.route('/api/user/info')
@login_required
def get_user_info():
    print(f'Getting user info for {current_user.email}')
    print(f'Meal allowance: {current_user.meal_allowance}')
    print(f'Credit balance: {current_user.credit_balance}')
    
    # Calculate total from cart items
    cart_total = sum(item.quantity * item.product.price for item in current_user.cart_items)
    
    cart_breakdown = current_user.calculate_payment_breakdown(cart_total)
    return jsonify({
        'email': current_user.email,
        'meal_allowance': current_user.meal_allowance,
        'credit_balance': current_user.credit_balance,
        'credit_card': current_user.credit_card,
        'cart': {
            'items': [{
                'id': item.id,
                'product': {
                    'id': item.product.id,
                    'name': item.product.name,
                    'price': item.product.price
                },
                'quantity': item.quantity,
                'subtotal': item.subtotal
            } for item in current_user.cart_items],
            'breakdown': cart_breakdown
        }
    })

@app.route('/api/cart/add', methods=['POST'])
@login_required
def add_to_cart():
    try:
        data = request.json
        print(f"Received add to cart request: {data}")
        
        product_id = data.get('product_id')
        quantity = data.get('quantity', 1)
        
        print(f"Product ID: {product_id}, Quantity: {quantity}")
        
        if not product_id or quantity < 1:
            print("Invalid product or quantity")
            return jsonify({'error': 'Invalid product or quantity'}), 400
        
        product = Product.query.get(product_id)
        if not product:
            print(f"Product not found with ID: {product_id}")
            return jsonify({'error': 'Product not found'}), 404
        
        print(f"Found product: {product.name}, price: {product.price}")
        
        # Check if product already in cart
        cart_item = Cart.query.filter_by(
            employee_id=current_user.id,
            product_id=product_id
        ).first()
        
        if cart_item:
            print(f"Updating existing cart item, old quantity: {cart_item.quantity}")
            cart_item.quantity += quantity
            print(f"New quantity: {cart_item.quantity}")
        else:
            print("Creating new cart item")
            cart_item = Cart(employee_id=current_user.id, product_id=product_id, quantity=quantity)
            db.session.add(cart_item)
        
        db.session.commit()
        print("Successfully added to cart")
        return jsonify({'message': 'Added to cart'})
    except Exception as e:
        print(f"Error in add_to_cart: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/cart/update', methods=['POST'])
@login_required
def update_cart():
    try:
        data = request.json
        item_id = data.get('item_id')
        quantity = int(data.get('quantity', 0))
        
        cart_item = Cart.query.get(item_id)
        if not cart_item or cart_item.employee_id != current_user.id:
            return jsonify({'error': 'Cart item not found'}), 404
        
        if quantity < 1:
            db.session.delete(cart_item)
        else:
            cart_item.quantity = quantity
        
        db.session.commit()
        return jsonify({'message': 'Cart updated'})
    except Exception as e:
        db.session.rollback()
        print(f'Error updating cart: {str(e)}')
        return jsonify({'error': 'Error updating cart', 'details': str(e)}), 500

@app.route('/api/cart/clear', methods=['POST'])
@login_required
def clear_cart():
    Cart.query.filter_by(employee_id=current_user.id).delete()
    db.session.commit()
    return jsonify({'message': 'Cart cleared'})

def process_credit_card_payment(amount, card_info):
    """Stub function to simulate credit card processing"""
    # In a real implementation, this would integrate with a payment processor
    # like Stripe, Square, etc.
    return {
        'success': True,
        'transaction_id': f'txn_{int(time.time())}',
        'amount': amount,
        'last4': card_info['last4']
    }

@app.route('/api/cart/place-order', methods=['POST'])
@login_required
def place_order():
    try:
        # Get user's cart
        cart_items = Cart.query.filter_by(employee_id=current_user.id).all()
        if not cart_items:
            return jsonify({'error': 'Cart is empty'}), 400

        # Calculate payment breakdown
        cart_total = sum(item.product.price * item.quantity for item in cart_items)
        tax = cart_total * 0.08
        grand_total = cart_total + tax

        # Get user's payment info
        employee = Employee.query.get(current_user.id)
        payment_breakdown = employee.calculate_payment_breakdown(grand_total)

        # Process credit card payment if needed
        if payment_breakdown['credit_card_charge'] > 0:
            payment_result = process_credit_card_payment(
                payment_breakdown['credit_card_charge'],
                employee.credit_card
            )
            if not payment_result['success']:
                return jsonify({'error': 'Payment processing failed'}), 400

        # Create order records
        order_time = datetime.utcnow()
        orders = []
        for item in cart_items:
            estimated_delivery = order_time + timedelta(minutes=30)
            order = Order(
                employee_id=current_user.id,
                product_id=item.product_id,
                quantity=item.quantity,
                price=item.product.price,
                order_time=order_time,
                estimated_delivery=estimated_delivery,
                allowance_used=payment_breakdown['allowance_used'] * (item.product.price * item.quantity / cart_total),
                credits_used=payment_breakdown['credits_used'] * (item.product.price * item.quantity / cart_total)
            )
            db.session.add(order)
            orders.append(order)

        # Update employee's allowance and credits
        employee.meal_allowance -= payment_breakdown['allowance_used']
        employee.credit_balance -= payment_breakdown['credits_used']

        # Clear the cart
        Cart.query.filter_by(employee_id=current_user.id).delete()

        # Commit all changes
        db.session.commit()

        return jsonify({
            'message': 'Order placed successfully',
            'order_id': orders[0].id,  # Return the first order's ID for tracking
            'order_time': order_time.isoformat(),
            'payment_breakdown': payment_breakdown
        })

    except Exception as e:
        db.session.rollback()
        print(f'Error placing order: {str(e)}')
        return jsonify({'error': 'Error placing order', 'details': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    if 'session_id' not in session:
        session['session_id'] = str(uuid4())
    
    data = request.json
    user_message = data.get('message', '')
    customer_email = session.get('customer_email')
    
    # Get AI response using session-specific RAG engine with customer context
    ai_response = get_ai_response(user_message, session['session_id'], customer_email)
    return jsonify({"response": ai_response})

@app.route('/api/products', methods=['GET'])
@login_required
def get_products():
    dietary_filters = request.args.getlist('dietary')
    allergen_excludes = request.args.getlist('exclude_allergens')
    
    products = Product.query.filter_by(available=True)
    
    if dietary_filters or allergen_excludes:
        filtered_products = []
        for p in products:
            dietary_info = json.loads(p.dietary_info or '{}')
            
            # Check dietary requirements
            meets_dietary = True
            for diet in dietary_filters:
                if not dietary_info.get(diet, False):
                    meets_dietary = False
                    break
            
            # Check allergens
            has_allergens = False
            product_allergens = dietary_info.get('allergens', [])
            for allergen in allergen_excludes:
                if allergen in product_allergens:
                    has_allergens = True
                    break
            
            if meets_dietary and not has_allergens:
                filtered_products.append(p)
    else:
        filtered_products = products.all()
    
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'description': p.description,
        'price': p.price,
        'category': p.category,
        'dietary_info': json.loads(p.dietary_info) if p.dietary_info else {}
    } for p in filtered_products])

@app.route('/api/support/tickets', methods=['GET'])
def get_support_tickets():
    """Get all support tickets. Can be filtered by status and type using query parameters."""
    status = request.args.get('status', 'open')
    ticket_type = request.args.get('type')
    
    query = SupportTicket.query
    
    if status:
        query = query.filter_by(status=status)
    if ticket_type:
        query = query.filter_by(ticket_type=ticket_type)
        
    tickets = query.order_by(SupportTicket.created_at.desc()).all()
    return jsonify([ticket.to_dict() for ticket in tickets])

@app.route('/api/support/tickets', methods=['POST'])
def create_support_ticket():
    """Create a new support ticket."""
    data = request.json
    
    if not data or not data.get('customer_email') or not data.get('issue'):
        return jsonify({'error': 'Missing required fields'}), 400
        
    ticket = SupportTicket(
        customer_email=data['customer_email'],
        issue=data['issue'],
        ticket_type=data.get('ticket_type', 'general'),
        order_id=data.get('order_id')
    )
    
    try:
        db.session.add(ticket)
        db.session.commit()
        return jsonify(ticket.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders', methods=['POST'])
@login_required
def create_order():
    data = request.json
    product_id = data['product_id']
    quantity = data.get('quantity', 1)
    customer_email = session.get('customer_email')
    
    # Get employee and check allowance
    employee = Employee.query.filter_by(email=customer_email).first()
    if not employee:
        return jsonify({"error": "Employee not found"}), 404
    
    total_amount = data['total_amount']
    allowance_to_use = min(employee.meal_allowance, total_amount)
    remaining_amount = total_amount - allowance_to_use
    credits_to_use = min(employee.credit_balance, remaining_amount)
    out_of_pocket = remaining_amount - credits_to_use
    
    if out_of_pocket > 0 and not data.get('confirm_payment', False):
        return jsonify({
            "warning": "Order exceeds allowance and credits",
            "out_of_pocket": out_of_pocket,
            "requires_confirmation": True
        }), 402
    
    # Create the order
    order = Order(
        customer_email=customer_email,
        products=json.dumps(data['products']),
        total_amount=total_amount,
        allowance_used=allowance_to_use,
        credits_used=credits_to_use,
        status='received',
        expense_code=data.get('expense_code'),
        delivery_estimate=datetime.now() + timedelta(hours=1)
    )
    
    # Update employee allowance and credits
    employee.meal_allowance -= allowance_to_use
    employee.credit_balance -= credits_to_use
    
    db.session.add(order)
    db.session.commit()
    
    return jsonify({
        "message": "Order created successfully",
        "order_id": order.id,
        "allowance_used": allowance_to_use,
        "credits_used": credits_to_use,
        "out_of_pocket": out_of_pocket
    })

@app.route('/init_db')
def init_db():
    try:
        # Clear all existing data
        db.session.query(Employee).delete()
        db.session.query(Cart).delete()
        db.session.query(Order).delete()
        db.session.query(Product).delete()
        
        db.session.commit()
        
        # Create sample employees
        print('Creating sample employees...')
        john = Employee(
            name='John Doe',
            email='john@techcorp.com',
            password='password123',  # Will be hashed by __init__
            meal_allowance=50.0,
            credit_balance=25.0,
            credit_card={
                'brand': 'Visa',
                'last4': '4242'
            }
        )
        print(f"John's password hash: {john.password_hash}")
        
        jane = Employee(
            name='Jane Smith',
            email='jane@techcorp.com',
            password='password123',  # Will be hashed by __init__
            meal_allowance=50.0,
            credit_balance=25.0,
            credit_card={
                'brand': 'Mastercard',
                'last4': '5555'
            }
        )
        employees = [john, jane]
        
        for employee in employees:
            print(f'Adding employee {employee.email} with meal_allowance={employee.meal_allowance}')
            db.session.add(employee)

        # Create sample products
        products = [
            Product(name='Burger', description='Classic beef burger', price=10.99),
            Product(name='Pizza', description='Margherita pizza', price=12.99),
            Product(name='Salad', description='Fresh garden salad', price=8.99),
            Product(name='Pasta', description='Spaghetti carbonara', price=11.99)
        ]
        
        for product in products:
            db.session.add(product)

        db.session.commit()

        # Create sample orders
        orders = [
            Order(
                employee_id=1,
                product_id=1,
                quantity=2,
                price=10.99,
                order_time=datetime.utcnow() - timedelta(days=1),
                status='order_delivered',
                allowance_used=21.98,
                credits_used=0.0,
                estimated_delivery=datetime.utcnow() - timedelta(hours=20)
            ),
            Order(
                employee_id=2,
                product_id=2,
                quantity=1,
                price=12.99,
                order_time=datetime.utcnow() - timedelta(hours=1),
                status='preparing_order',
                allowance_used=12.99,
                credits_used=0.0,
                estimated_delivery=datetime.utcnow() + timedelta(minutes=15)
            )
        ]
        
        for order in orders:
            db.session.add(order)

        db.session.commit()
        
        # Verify employees were created correctly
        for email in ['john@techcorp.com', 'jane@techcorp.com']:
            emp = Employee.query.filter_by(email=email).first()
            print(f'Verifying {email}: meal_allowance={emp.meal_allowance}, credit_balance={emp.credit_balance}')
        
        return jsonify({'message': 'Database initialized with sample products and orders'})
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/employee/allowance', methods=['GET'])
def get_allowance():
    customer_email = session.get('customer_email')
    if not customer_email:
        return jsonify({"error": "Not logged in"}), 401
    
    # Use the logged-in user
    employee = current_user
    
    company = Company.query.get(employee.company_id)
    
    return jsonify({
        "meal_allowance": employee.meal_allowance,
        "credit_balance": employee.credit_balance,
        "refresh_cycle": company.meal_allowance_refresh_cycle,
        "default_allowance": company.default_meal_allowance
    })

@app.route('/api/orders/<int:order_id>/cancel', methods=['POST'])
def cancel_order():
    order_id = request.view_args['order_id']
    customer_email = session.get('customer_email')
    
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    
    if order.customer_email != customer_email:
        return jsonify({"error": "Unauthorized"}), 403
    
    if order.status not in ['received', 'in preparation']:
        return jsonify({"error": "Order cannot be cancelled"}), 400
    
    # Refund allowance and credits
    employee = Employee.query.filter_by(email=customer_email).first()
    if employee:
        employee.meal_allowance += order.allowance_used
        employee.credit_balance += order.credits_used
    
    # Update order status
    order.status = 'cancelled'
    order.cancellation_reason = request.json.get('reason')
    
    db.session.commit()
    
    return jsonify({"message": "Order cancelled successfully"})

@app.route('/api/orders/<int:order_id>/refund', methods=['POST'])
def request_refund():
    order_id = request.view_args['order_id']
    customer_email = session.get('customer_email')
    
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    
    if order.customer_email != customer_email:
        return jsonify({"error": "Unauthorized"}), 403
    
    if order.status != 'delivered':
        return jsonify({"error": "Order must be delivered to request refund"}), 400
    
    # Create refund request
    refund = Refund(
        order_id=order_id,
        amount=request.json.get('amount', order.total_amount),
        reason=request.json['reason'],
        credit_amount=request.json.get('credit_amount', 0.0)
    )
    
    db.session.add(refund)
    db.session.commit()
    
    return jsonify({
        "message": "Refund request submitted successfully",
        "refund_id": refund.id
    })

# Create all tables after model definitions
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
