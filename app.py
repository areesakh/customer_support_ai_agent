from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import openai
import os
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-key-123'  # Use a fixed key for development
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shop.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = False  # Allow non-HTTPS for development

db = SQLAlchemy(app)

# Database Models
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_email = db.Column(db.String(120), nullable=False)
    products = db.Column(db.Text, nullable=False)  # Stored as JSON
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Ensure the instance folder exists
if not os.path.exists('instance'):
    os.makedirs('instance')

# Initialize RAG engines for different sessions
from rag_engine import RAGEngine
from uuid import uuid4

rag_engines = {}

def get_or_create_rag_engine(session_id):
    if session_id not in rag_engines:
        rag_engines[session_id] = RAGEngine('support_sop.txt')
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
def home():
    # Create a new session ID if not exists
    if 'session_id' not in session:
        session['session_id'] = str(uuid4())
    return render_template('index.html')

@app.route('/api/set_email', methods=['POST'])
def set_email():
    data = request.json
    email = data.get('email')
    if email:
        session['customer_email'] = email
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Email is required'})

@app.route('/api/chat', methods=['POST'])
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
def get_products():
    products = Product.query.all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'description': p.description,
        'price': p.price,
        'category': p.category
    } for p in products])

@app.route('/api/orders', methods=['POST'])
def create_order():
    data = request.json
    order = Order(
        customer_email=data['email'],
        products=json.dumps(data['products']),
        total_amount=data['total_amount'],
        status='pending'
    )
    db.session.add(order)
    db.session.commit()
    return jsonify({"message": "Order created successfully", "order_id": order.id})

@app.route('/init_db', methods=['GET', 'POST'])
def init_db():
    try:
        # Clear existing data
        Order.query.delete()
        Product.query.delete()
        
        # Add sample products
        products = [
            Product(name='Premium Support Package', description='24/7 priority customer support with dedicated agent', price=199.99, category='Service'),
            Product(name='Basic Support Plan', description='Standard customer support during business hours', price=49.99, category='Service'),
            Product(name='Custom Integration', description='Custom software integration with your existing systems', price=999.99, category='Service'),
            Product(name='AI Chatbot License', description='License for AI-powered customer service chatbot', price=299.99, category='Software'),
            Product(name='Training Package', description='Comprehensive training for your support team', price=399.99, category='Training')
        ]
        
        for product in products:
            db.session.add(product)
        db.session.commit()
        
        # Add sample orders
        sample_orders = [
            {
                'email': 'john@example.com',
                'products': json.dumps([
                    {'id': 1, 'name': 'Premium Support Package', 'quantity': 1},
                    {'id': 4, 'name': 'AI Chatbot License', 'quantity': 1}
                ]),
                'total': 499.98,
                'status': 'completed',
                'created_at': datetime.now() - timedelta(days=2)
            },
            {
                'email': 'sarah@example.com',
                'products': json.dumps([
                    {'id': 2, 'name': 'Basic Support Plan', 'quantity': 1}
                ]),
                'total': 49.99,
                'status': 'processing',
                'created_at': datetime.now() - timedelta(hours=12)
            },
            {
                'email': 'test@example.com',
                'products': json.dumps([
                    {'id': 3, 'name': 'Custom Integration', 'quantity': 1},
                    {'id': 5, 'name': 'Training Package', 'quantity': 1}
                ]),
                'total': 1399.98,
                'status': 'pending',
                'created_at': datetime.now() - timedelta(hours=1)
            }
        ]
        
        for order_data in sample_orders:
            order = Order(
                customer_email=order_data['email'],
                products=order_data['products'],
                total_amount=order_data['total'],
                status=order_data['status'],
                created_at=order_data['created_at']
            )
            db.session.add(order)
        
        db.session.commit()
        return jsonify({'message': 'Database initialized with sample products and orders'})
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5001)
