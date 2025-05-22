from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import boto3
from boto3.dynamodb.conditions import Key, Attr
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bcrypt import hashpw, gensalt, checkpw
import os
from datetime import datetime
from decimal import Decimal
from functools import wraps

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Load sensitive info from environment variables
AWS_REGION = os.getenv('AWS_REGION', 'ap-south-1')
SENDER_EMAIL = os.getenv("SENDER_EMAIL")  # Set in your environment
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")  # App password
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN")  # Set this in your environment

dynamodb = boto3.resource('dynamodb', region_name='ap-south-1')
sns = boto3.client('sns', region_name='ap-south-1')

# DynamoDB Tables
users_table = dynamodb.Table('FusionFurnishUsers')
design_requests_table = dynamodb.Table('FusionFurnishDesignRequests')
contact_messages_table = dynamodb.Table('FusionFurnishContactMessages')
orders_table = dynamodb.Table('FusionFurnishOrders')
wishlist_table = dynamodb.Table('FusionFurnishWishlists')
consultation_table = dynamodb.Table('FusionFurnishConsultations')
products_table = dynamodb.Table('FusionFurnishProducts')
admin_users_table = dynamodb.Table('FusionFurnishAdminUsers')

# SNS Topic ARN - MAKE SURE THIS LINE EXISTS AND IS CORRECT
sns_topic_arn = 'arn:aws:sns:ap-south-1:601457281445:FFNotifications'

# Email Configuration
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

def send_email(to_email, subject, body):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        print("Email sent successfully.")
    except Exception as e:
        print(f"Failed to send email: {e}")

# Utility functions
def is_logged_in():
    return 'user_email' in session

def is_admin():
    if 'admin_email' not in session:
        return False
    response = admin_users_table.get_item(Key={'email': session['admin_email']})
    return 'Item' in response

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin():
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


@app.route('/')
def home():
    return render_template('base.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        phone = request.form.get('phone', '')  # Optional phone number

        if not all([name, email, password, confirm_password]):
            return "All fields are mandatory!"
        if password != confirm_password:
            return "Passwords do not match!"

        response = users_table.get_item(Key={'email': email})
        if 'Item' in response:
            return "User already exists!"

        hashed_password = hashpw(password.encode('utf-8'), gensalt()).decode('utf-8')
        users_table.put_item(Item={
            'email': email,
            'name': name,
            'password': hashed_password,
            'phone': phone,
            'login_count': 0,
            'created_at': datetime.now().isoformat(),
            'profile_picture': '',  # Empty string for default profile picture
            'address': '',
            'preferences': {}
        })

        try:
            sns.publish(
                TopicArn=sns_topic_arn,
                Message=f'New FusionFurnish user: {name} ({email})',
                Subject='New User Registration'
            )
        except Exception as e:
            print(f"Failed to send SNS notification: {e}")

        send_email(email, "Welcome to FusionFurnish", 
                  f"Hello {name},\n\nWelcome to FusionFurnish! Your account has been created successfully.\n\nRegards,\nFusionFurnish Team")

        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        if not email or not password:
            return "Please fill in all fields."

        response = users_table.get_item(Key={'email': email})
        user = response.get('Item')

        if not user or not checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            return "Invalid credentials."

        users_table.update_item(
            Key={'email': email},
            UpdateExpression='SET login_count = login_count + :inc, last_login = :time',
            ExpressionAttributeValues={
                ':inc': 1,
                ':time': datetime.now().isoformat()
            }
        )
        
        # Store user info in session
        session['user_email'] = email
        session['user_name'] = user['name']
        
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user_email = session.get('user_email')
    response = users_table.get_item(Key={'email': user_email})
    user = response.get('Item', {})
    
    # Get user's design requests - FIXED
    try:
        response = design_requests_table.scan(
            FilterExpression=Attr('email').eq(user_email)
        )
        design_requests = response.get('Items', [])
    except Exception as e:
        print(f"Error fetching design requests: {e}")
        design_requests = []
    
    # Get user's orders - FIXED
    try:
        response = orders_table.scan(
            FilterExpression=Attr('customer_email').eq(user_email)
        )
        orders = response.get('Items', [])
    except Exception as e:
        print(f"Error fetching orders: {e}")
        orders = []
    
    # Get user's consultations - FIXED
    try:
        response = consultation_table.scan(
            FilterExpression=Attr('customer_email').eq(user_email)
        )
        consultations = response.get('Items', [])
    except Exception as e:
        print(f"Error fetching consultations: {e}")
        consultations = []
    
    return render_template('dashboard.html', 
                           user=user, 
                           design_requests=design_requests,
                           orders=orders,
                           consultations=consultations)
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_email = session.get('user_email')
    
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        address = request.form['address']
        
        # Update user information
        users_table.update_item(
            Key={'email': user_email},
            UpdateExpression='SET #n = :name, phone = :phone, address = :address',
            ExpressionAttributeNames={
                '#n': 'name'  # 'name' is a reserved keyword in DynamoDB
            },
            ExpressionAttributeValues={
                ':name': name,
                ':phone': phone,
                ':address': address
            }
        )
        
        session['user_name'] = name
        return redirect(url_for('profile'))
    
    # Get user data
    response = users_table.get_item(Key={'email': user_email})
    user = response.get('Item', {})
    
    return render_template('profile.html', user=user)

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    user_email = session.get('user_email')
    
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        
        if new_password != confirm_password:
            return "New passwords do not match!"
        
        # Verify current password
        response = users_table.get_item(Key={'email': user_email})
        user = response.get('Item')
        
        if not checkpw(current_password.encode('utf-8'), user['password'].encode('utf-8')):
            return "Current password is incorrect!"
        
        # Update password
        hashed_password = hashpw(new_password.encode('utf-8'), gensalt()).decode('utf-8')
        users_table.update_item(
            Key={'email': user_email},
            UpdateExpression='SET password = :password',
            ExpressionAttributeValues={
                ':password': hashed_password
            }
        )
        
        send_email(user_email, "Password Changed", 
                  f"Hello {user['name']},\n\nYour password has been changed successfully.\n\nRegards,\nFusionFurnish Team")
        
        return redirect(url_for('profile'))
    
    return render_template('change_password.html')

@app.route('/furniture-catalog')
def furniture_catalog():
    category = request.args.get('category', None)
    search = request.args.get('search', None)
    
    # In production this would query the products_table
    furniture_items = [
        {'id': '1', 'name': 'Modern Sofa', 'category': 'Living Room', 'price': 24999, 'image': 'sofa.jpg'},
        {'id': '2', 'name': 'Study Desk', 'category': 'Office', 'price': 12499, 'image': 'desk.jpg'},
        {'id': '3', 'name': 'Dining Table Set', 'category': 'Dining', 'price': 34999, 'image': 'dining.jpg'},
        {'id': '4', 'name': 'Leather Recliner', 'category': 'Living Room', 'price': 19999, 'image': 'recliner.jpg'},
        {'id': '5', 'name': 'Queen Size Bed', 'category': 'Bedroom', 'price': 28499, 'image': 'bed.jpg'},
    ]
    
    # Filter by category if provided
    if category:
        furniture_items = [item for item in furniture_items if item['category'] == category]
    
    # Filter by search term if provided
    if search:
        search = search.lower()
        furniture_items = [item for item in furniture_items if search in item['name'].lower() or search in item['category'].lower()]
    
    categories = list(set(item['category'] for item in furniture_items))
    
    return render_template('catalog.html', items=furniture_items, categories=categories)

@app.route('/product/<product_id>')
def product_detail(product_id):
    # In production this would query the products_table
    furniture_items = {
        '1': {'id': '1', 'name': 'Modern Sofa', 'category': 'Living Room', 'price': 24999, 'description': 'A comfortable modern sofa with plush cushions and durable upholstery.', 'image': 'sofa.jpg'},
        '2': {'id': '2', 'name': 'Study Desk', 'category': 'Office', 'price': 12499, 'description': 'A spacious study desk with drawers for organization and a sleek finish.', 'image': 'desk.jpg'},
        '3': {'id': '3', 'name': 'Dining Table Set', 'category': 'Dining', 'price': 34999, 'description': 'An elegant dining set with a table and six chairs, perfect for family gatherings.', 'image': 'dining.jpg'},
        '4': {'id': '4', 'name': 'Leather Recliner', 'category': 'Living Room', 'price': 19999, 'description': 'A luxurious leather recliner with multiple positions for ultimate comfort.', 'image': 'recliner.jpg'},
        '5': {'id': '5', 'name': 'Queen Size Bed', 'category': 'Bedroom', 'price': 28499, 'description': 'A sturdy queen size bed with a beautiful headboard and ample storage space.', 'image': 'bed.jpg'},
    }
    
    product = furniture_items.get(product_id)
    if not product:
        return "Product not found", 404
    
    return render_template('product_detail.html', product=product)

@app.route('/custom-request', methods=['GET', 'POST'])
@login_required
def custom_request():
    if request.method == 'POST':
        email = session.get('user_email')
        name = session.get('user_name')
        phone = request.form['phone']
        furniture_type = request.form['furniture_type']
        description = request.form['description']
        request_id = str(uuid.uuid4())

        design_requests_table.put_item(Item={
            'email': email,
            'request_id': request_id,
            'name': name,
            'phone': phone,
            'furniture_type': furniture_type,
            'description': description,
            'status': 'Pending',
            'created_at': datetime.now().isoformat()
        })

        thank_you = f"Hi {name},\n\nThanks for requesting a custom {furniture_type} design. We'll contact you shortly.\n\nRequest ID: {request_id}\n\nRegards,\nFusionFurnish Team"
        send_email(email, "FusionFurnish Design Request Received", thank_you)

        admin_alert = f"New design request from {name} ({email}):\nRequest ID: {request_id}\nType: {furniture_type}\nDescription: {description}"
        send_email(SENDER_EMAIL, "New Custom Furniture Request", admin_alert)

        return redirect(url_for('dashboard'))

    return render_template('custom-request.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        subject = request.form['subject']
        message = request.form['message']
        message_id = str(uuid.uuid4())
        
        # Store message in DynamoDB
        contact_messages_table.put_item(Item={
            'message_id': message_id,
            'name': name,
            'email': email,
            'subject': subject,
            'message': message,
            'created_at': datetime.now().isoformat(),
            'status': 'Unread'
        })
        
        # Send email notification
        admin_message = f"New contact message from {name} ({email}):\n\nSubject: {subject}\n\nMessage: {message}"
        send_email(SENDER_EMAIL, f"New Contact Message: {subject}", admin_message)
        
        # Send confirmation to user
        user_confirmation = f"Hello {name},\n\nThank you for contacting FusionFurnish. We have received your message and will get back to you shortly.\n\nRegards,\nFusionFurnish Team"
        send_email(email, "FusionFurnish Contact Confirmation", user_confirmation)
        
        return redirect(url_for('contact_success'))
        
    return render_template('contact.html')

@app.route('/contact-success')
def contact_success():
    return render_template('contact_success.html')

@app.route('/faq')
def faq():
    faqs = [
        {
            'question': 'How long does custom furniture take to make?',
            'answer': 'Custom furniture typically takes 4-6 weeks from design approval to delivery.'
        },
        {
            'question': 'Do you offer installation services?',
            'answer': 'Yes, we provide professional installation for all our furniture at a nominal fee.'
        },
        {
            'question': 'What materials do you use?',
            'answer': 'We use premium quality sustainable wood, metals, and fabrics sourced from trusted suppliers.'
        }
    ]
    return render_template('faq.html', faqs=faqs)

@app.route('/testimonials')
def testimonials():
    # This would be replaced by real testimonials from a database in production
    testimonials = [
        {
            'name': 'Amit Sharma',
            'rating': 5,
            'message': 'The custom dining table exceeded my expectations. Excellent craftsmanship!'
        },
        {
            'name': 'Priya Patel',
            'rating': 4,
            'message': 'Great service and beautiful furniture. Will order again.'
        }
    ]
    return render_template('testimonials.html', testimonials=testimonials)

# =========== WISHLIST FUNCTIONALITY ===========

@app.route('/wishlist')
@login_required
def wishlist():
    user_email = session.get('user_email')
    
    # Get user's wishlist items
    response = wishlist_table.query(
        KeyConditionExpression=Key('user_email').eq(user_email)
    )
    wishlist_items = response.get('Items', [])
    
    # In production, you would fetch product details from products_table
    # This is a simplified version with mock data
    product_map = {
        '1': {'id': '1', 'name': 'Modern Sofa', 'category': 'Living Room', 'price': 24999, 'image': 'sofa.jpg'},
        '2': {'id': '2', 'name': 'Study Desk', 'category': 'Office', 'price': 12499, 'image': 'desk.jpg'},
        '3': {'id': '3', 'name': 'Dining Table Set', 'category': 'Dining', 'price': 34999, 'image': 'dining.jpg'},
        '4': {'id': '4', 'name': 'Leather Recliner', 'category': 'Living Room', 'price': 19999, 'image': 'recliner.jpg'},
        '5': {'id': '5', 'name': 'Queen Size Bed', 'category': 'Bedroom', 'price': 28499, 'image': 'bed.jpg'},
    }
    
    # Enhance wishlist items with product details
    for item in wishlist_items:
        if item['product_id'] in product_map:
            item.update(product_map[item['product_id']])
    
    return render_template('wishlist.html', wishlist_items=wishlist_items)

@app.route('/add_to_wishlist/<product_id>', methods=['POST'])
@login_required
def add_to_wishlist(product_id):
    user_email = session.get('user_email')
    
    # Check if product already in wishlist
    response = wishlist_table.get_item(
        Key={
            'user_email': user_email,
            'product_id': product_id
        }
    )
    
    if 'Item' not in response:
        # Add to wishlist
        wishlist_table.put_item(Item={
            'user_email': user_email,
            'product_id': product_id,
            'added_on': datetime.now().isoformat()
        })
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'success'})
    
    return redirect(url_for('wishlist'))

@app.route('/remove_from_wishlist/<product_id>', methods=['POST'])
@login_required
def remove_from_wishlist(product_id):
    user_email = session.get('user_email')
    
    # Remove from wishlist
    wishlist_table.delete_item(
        Key={
            'user_email': user_email,
            'product_id': product_id
        }
    )
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'success'})
    
    return redirect(url_for('wishlist'))

# =========== ORDER MANAGEMENT SYSTEM ===========

@app.route('/orders')
@login_required
def user_orders():
    user_email = session.get('user_email')
    
    # Get user's orders
    response = orders_table.query(
        KeyConditionExpression=Key('customer_email').eq(user_email)
    )
    orders = response.get('Items', [])
    
    return render_template('orders.html', orders=orders)

@app.route('/order/<order_id>')
@login_required
def order_detail(order_id):
    user_email = session.get('user_email')
    
    # Get order details
    response = orders_table.get_item(
        Key={
            'customer_email': user_email,
            'order_id': order_id
        }
    )
    
    if 'Item' not in response:
        return "Order not found", 404
    
    order = response['Item']
    
    return render_template('order_detail.html', order=order)

@app.route('/place_order', methods=['POST'])
@login_required
def place_order():
    user_email = session.get('user_email')
    user_name = session.get('user_name')
    
    # In production, this would get data from a shopping cart
    # For demonstration, we'll create a sample order
    products = request.form.getlist('products[]')
    quantities = request.form.getlist('quantities[]')
    
    items = []
    total_amount = 0
    
    # Mock product data - in production this would come from your products table
    product_map = {
        '1': {'id': '1', 'name': 'Modern Sofa', 'price': 24999},
        '2': {'id': '2', 'name': 'Study Desk', 'price': 12499},
        '3': {'id': '3', 'name': 'Dining Table Set', 'price': 34999},
        '4': {'id': '4', 'name': 'Leather Recliner', 'price': 19999},
        '5': {'id': '5', 'name': 'Queen Size Bed', 'price': 28499},
    }
    
    for product_id, quantity in zip(products, quantities):
        quantity = int(quantity)
        product = product_map.get(product_id, {})
        if product and quantity > 0:
            item_price = product['price']
            item_total = item_price * quantity
            total_amount += item_total
            
            items.append({
                'product_id': product_id,
                'name': product['name'],
                'quantity': quantity,
                'unit_price': item_price,
                'total': item_total
            })
    
    if not items:
        return "No valid items in order", 400
    
    # Get customer shipping details
    response = users_table.get_item(Key={'email': user_email})
    user = response.get('Item', {})
    
    shipping_address = request.form.get('shipping_address', user.get('address', ''))
    phone = request.form.get('phone', user.get('phone', ''))
    
    # Create order
    order_id = str(uuid.uuid4())
    current_time = datetime.now().isoformat()
    
    order = {
        'order_id': order_id,
        'customer_email': user_email,
        'customer_name': user_name,
        'shipping_address': shipping_address,
        'phone': phone,
        'items': items,
        'total_amount': total_amount,
        'status': 'Placed',
        'payment_status': 'Pending',
        'created_at': current_time,
        'updated_at': current_time
    }
    
    # Save order to DynamoDB
    orders_table.put_item(Item=order)
    
    # Send order confirmation email
    order_confirmation = f"""Hello {user_name},

Thank you for your order with FusionFurnish!

Order ID: {order_id}
Total Amount: ₹{total_amount}

Your order has been placed successfully and is being processed. We will notify you when it ships.

Regards,
FusionFurnish Team
"""
    send_email(user_email, "FusionFurnish Order Confirmation", order_confirmation)
    
    # Notify admin about new order
    admin_notification = f"""New order received:

Order ID: {order_id}
Customer: {user_name} ({user_email})
Total Amount: ₹{total_amount}
Items: {len(items)}
"""
    send_email(SENDER_EMAIL, f"New Order: {order_id}", admin_notification)
    
    return redirect(url_for('order_detail', order_id=order_id))

@app.route('/quick_order/<product_id>', methods=['POST'])
@login_required
def quick_order(product_id):
    # Place an order directly from product page or wishlist
    user_email = session.get('user_email')
    user_name = session.get('user_name')
    
    # Mock product data
    product_map = {
        '1': {'id': '1', 'name': 'Modern Sofa', 'price': 24999},
        '2': {'id': '2', 'name': 'Study Desk', 'price': 12499},
        '3': {'id': '3', 'name': 'Dining Table Set', 'price': 34999},
        '4': {'id': '4', 'name': 'Leather Recliner', 'price': 19999},
        '5': {'id': '5', 'name': 'Queen Size Bed', 'price': 28499},
    }
    
    product = product_map.get(product_id)
    if not product:
        return "Product not found", 404
    
    # Get customer details
    response = users_table.get_item(Key={'email': user_email})
    user = response.get('Item', {})
    
    shipping_address = user.get('address', '')
    phone = user.get('phone', '')
    
    # Create order
    order_id = str(uuid.uuid4())
    current_time = datetime.now().isoformat()
    
    order = {
        'order_id': order_id,
        'customer_email': user_email,
        'customer_name': user_name,
        'shipping_address': shipping_address,
        'phone': phone,
        'items': [{
            'product_id': product_id,
            'name': product['name'],
            'quantity': 1,
            'unit_price': product['price'],
            'total': product['price']
        }],
        'total_amount': product['price'],
        'status': 'Placed',
        'payment_status': 'Pending',
        'created_at': current_time,
        'updated_at': current_time
    }
    
    # Save order to DynamoDB
    orders_table.put_item(Item=order)
    
    # Send order confirmation email
    order_confirmation = f"""Hello {user_name},

Thank you for your order with FusionFurnish!

Order ID: {order_id}
Item: {product['name']}
Total Amount: ₹{product['price']}

Your order has been placed successfully and is being processed. We will notify you when it ships.

Regards,
FusionFurnish Team
"""
    send_email(user_email, "FusionFurnish Order Confirmation", order_confirmation)
    
    return redirect(url_for('order_detail', order_id=order_id))

@app.route('/cancel_order/<order_id>', methods=['POST'])
@login_required
def cancel_order(order_id):
    user_email = session.get('user_email')
    
    # Check if order exists and belongs to user
    response = orders_table.get_item(
        Key={
            'customer_email': user_email,
            'order_id': order_id
        }
    )
    
    if 'Item' not in response:
        return "Order not found", 404
    
    order = response['Item']
    
    # Check if order can be cancelled (not delivered or shipped)
    if order['status'] in ['Delivered', 'Shipped']:
        return "Cannot cancel order that has been shipped or delivered", 400
    
    # Update order status
    orders_table.update_item(
        Key={
            'customer_email': user_email,
            'order_id': order_id
        },
        UpdateExpression='SET #status = :status, updated_at = :updated_at',
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': 'Cancelled',
            ':updated_at': datetime.now().isoformat()
        }
    )
    
    # Send cancellation email
    cancellation_email = f"""Hello {order['customer_name']},

Your order (ID: {order_id}) has been cancelled as requested.

If you have any questions, please contact our customer service.

Regards,
FusionFurnish Team
"""
    send_email(user_email, "FusionFurnish Order Cancellation", cancellation_email)
    
    return redirect(url_for('order_detail', order_id=order_id))

# =========== INTERIOR DESIGN CONSULTATION ===========

@app.route('/design-consultation', methods=['GET', 'POST'])
@login_required
def design_consultation():
    if request.method == 'POST':
        user_email = session.get('user_email')
        user_name = session.get('user_name')
        
        preferred_date = request.form['preferred_date']
        preferred_time = request.form['preferred_time']
        consultation_type = request.form['consultation_type']
        room_type = request.form['room_type']
        special_requirements = request.form.get('special_requirements', '')
        phone = request.form.get('phone', '')
        
        # Create a unique ID for the consultation
        consultation_id = str(uuid.uuid4())
        
        # Store consultation request in DynamoDB
        consultation_table.put_item(Item={
            'consultation_id': consultation_id,
            'customer_email': user_email,
            'customer_name': user_name,
            'phone': phone,
            'preferred_date': preferred_date,
            'preferred_time': preferred_time,
            'consultation_type': consultation_type,
            'room_type': room_type,
            'special_requirements': special_requirements,
            'status': 'Requested',
            'created_at': datetime.now().isoformat()
        })
        
        # Send confirmation email to user
        confirmation_email = f"""Hello {user_name},

Thank you for scheduling a design consultation with FusionFurnish!

Consultation ID: {consultation_id}
Date: {preferred_date}
Time: {preferred_time}
Type: {consultation_type}
Room: {room_type}

We will confirm this appointment shortly. If you need to reschedule, please contact us.

Regards,
FusionFurnish Team
"""
        send_email(user_email, "FusionFurnish Design Consultation Scheduled", confirmation_email)
        
        # Notify admin about new consultation request
        admin_notification = f"""New design consultation request:

Consultation ID: {consultation_id}
Customer: {user_name} ({user_email})
Phone: {phone}
Date: {preferred_date}
Time: {preferred_time}
Type: {consultation_type}
Room: {room_type}
Special Requirements: {special_requirements}
"""
        send_email(SENDER_EMAIL, "New Design Consultation Request", admin_notification)
        
        return redirect(url_for('dashboard'))
        
    return render_template('design_consultation.html')

@app.route('/consultation/<consultation_id>')
@login_required
def consultation_detail(consultation_id):
    user_email = session.get('user_email')
    
    # Get consultation details
    response = consultation_table.scan(
        FilterExpression=Attr('consultation_id').eq(consultation_id) & 
                        Attr('customer_email').eq(user_email)
    )
    
    if not response['Items']:
        return "Consultation not found", 404
    
    consultation = response['Items'][0]
    
    return render_template('consultation_detail.html', consultation=consultation)

@app.route('/cancel_consultation/<consultation_id>', methods=['POST'])
@login_required
def cancel_consultation(consultation_id):
    user_email = session.get('user_email')
    
    # Find consultation
    response = consultation_table.scan(
        FilterExpression=Attr('consultation_id').eq(consultation_id) & 
                        Attr('customer_email').eq(user_email)
    )
    
    if not response['Items']:
        return "Consultation not found", 404
    
    consultation = response['Items'][0]
    
    # Update consultation status
    consultation_table.update_item(
        Key={
            'consultation_id': consultation_id,
            'customer_email': user_email
        },
        UpdateExpression='SET #status = :status, updated_at = :updated_at',
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': 'Cancelled',
            ':updated_at': datetime.now().isoformat()
        }
    )
    
    # Send cancellation email
    cancellation_email = f"""Hello {consultation['customer_name']},

Your design consultation (ID: {consultation_id}) scheduled for {consultation['preferred_date']} at {consultation['preferred_time']} has been cancelled as requested.

If you wish to reschedule, please book a new consultation.

Regards,
FusionFurnish Team
"""
    send_email(user_email, "FusionFurnish Consultation Cancellation", cancellation_email)
    
    return redirect(url_for('dashboard'))

# =========== ADMIN FUNCTIONALITY ===========

@app.route('/admin')
@admin_required
def admin_dashboard():
    # Count total users
    user_count = users_table.scan(
        Select='COUNT'
    )['Count']
    
    # Count total orders
    order_count = orders_table.scan(
        Select='COUNT'
    )['Count']
    
    # Count total consultations
    consultation_count = consultation_table.scan(
        Select='COUNT'
    )['Count']
    
    # Count total custom design requests
    design_request_count = design_requests_table.scan(
        Select='COUNT'
    )['Count']
    
    # Get recent orders
    recent_orders = orders_table.scan(
        Limit=5,
        ScanIndexForward=False  # This would work better with a GSI in production
    )['Items']
    
    # Get pending consultations
    pending_consultations = consultation_table.scan(
        FilterExpression=Attr('status').eq('Requested'),
        Limit=5
    )['Items']
    
    return render_template('admin/dashboard.html', 
                           user_count=user_count,
                           order_count=order_count,
                           consultation_count=consultation_count,
                           design_request_count=design_request_count,
                           recent_orders=recent_orders,
                           pending_consultations=pending_consultations)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        response = admin_users_table.get_item(Key={'email': email})
        if 'Item' not in response:
            return "Invalid credentials", 401
        
        admin_user = response['Item']
        if not checkpw(password.encode('utf-8'), admin_user['password'].encode('utf-8')):
            return "Invalid credentials", 401
        
        session['admin_email'] = email
        session['admin_name'] = admin_user['name']
        
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_email', None)
    session.pop('admin_name', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/users')
@admin_required
def admin_users():
    # Scan for all users
    response = users_table.scan()
    users = response['Items']
    
    return render_template('admin/users.html', users=users)

@app.route('/admin/orders')
@admin_required
def admin_orders():
    # Scan for all orders
    response = orders_table.scan()
    orders = response['Items']
    
    return render_template('admin/orders.html', orders=orders)

@app.route('/admin/update_order_status', methods=['POST'])
@admin_required
def update_order_status():
    order_id = request.form['order_id']
    customer_email = request.form['customer_email']
    new_status = request.form['status']
    
    # Update order status
    orders_table.update_item(
        Key={
            'customer_email': customer_email,
            'order_id': order_id
        },
        UpdateExpression='SET #status = :status, updated_at = :updated_at',
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': new_status,
            ':updated_at': datetime.now().isoformat()
        }
    )
    
    # Get order details
    response = orders_table.get_item(
        Key={
            'customer_email': customer_email,
            'order_id': order_id
        }
    )
    order = response['Item']
    
    # Send status update email to customer
    status_update_email = f"""Hello {order['customer_name']},

The status of your order (ID: {order_id}) has been updated to: {new_status}

{get_status_message(new_status)}

Thank you for choosing FusionFurnish!

Regards,
FusionFurnish Team
"""
    send_email(customer_email, f"FusionFurnish Order Status Update: {new_status}", status_update_email)
    
    return redirect(url_for('admin_orders'))

def get_status_message(status):
    messages = {
        'Processing': 'Your order is being processed. We will notify you when it ships.',
        'Shipped': 'Your order has been shipped and is on its way to you.',
        'Delivered': 'Your order has been delivered. We hope you enjoy your FusionFurnish products!',
        'Cancelled': 'Your order has been cancelled as requested.',
        'On Hold': 'Your order is currently on hold. Our team will contact you shortly.'
    }
    return messages.get(status, '')

@app.route('/admin/consultations')
@admin_required
def admin_consultations():
    # Scan for all consultations
    response = consultation_table.scan()
    consultations = response['Items']
    
    return render_template('admin/consultations.html', consultations=consultations)

@app.route('/admin/update_consultation_status', methods=['POST'])
@admin_required
def update_consultation_status():
    consultation_id = request.form['consultation_id']
    customer_email = request.form['customer_email']
    new_status = request.form['status']
    notes = request.form.get('notes', '')
    
    # Update consultation status
    consultation_table.update_item(
        Key={
            'consultation_id': consultation_id,
            'customer_email': customer_email
        },
        UpdateExpression='SET #status = :status, notes = :notes, updated_at = :updated_at',
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': new_status,
            ':notes': notes,
            ':updated_at': datetime.now().isoformat()
        }
    )
    
    # Get consultation details
    response = consultation_table.get_item(
        Key={
            'consultation_id': consultation_id,
            'customer_email': customer_email
        }
    )
    consultation = response['Item']
    
    # Send status update email to customer
    if new_status == 'Confirmed':
        status_update_email = f"""Hello {consultation['customer_name']},

Your design consultation (ID: {consultation_id}) has been confirmed for:

Date: {consultation['preferred_date']}
Time: {consultation['preferred_time']}

{notes if notes else ''}

We look forward to helping you with your interior design project!

Regards,
FusionFurnish Team
"""
        send_email(customer_email, "FusionFurnish Consultation Confirmed", status_update_email)
    
    return redirect(url_for('admin_consultations'))

@app.route('/admin/design-requests')
@admin_required
def admin_design_requests():
    # Scan for all design requests
    response = design_requests_table.scan()
    design_requests = response['Items']
    
    return render_template('admin/design_requests.html', design_requests=design_requests)

@app.route('/admin/update_design_request', methods=['POST'])
@admin_required
def update_design_request():
    email = request.form['email']
    request_id = request.form['request_id']
    new_status = request.form['status']
    response_notes = request.form.get('response', '')
    
    # Update design request status
    design_requests_table.update_item(
        Key={
            'email': email,
            'request_id': request_id
        },
        UpdateExpression='SET #status = :status, response = :response, updated_at = :updated_at',
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': new_status,
            ':response': response_notes,
            ':updated_at': datetime.now().isoformat()
        }
    )
    
    # Get design request details
    response = design_requests_table.get_item(
        Key={
            'email': email,
            'request_id': request_id
        }
    )
    design_request = response['Item']
    
    # Send status update email to customer
    status_update_email = f"""Hello {design_request['name']},

The status of your custom furniture design request (ID: {request_id}) has been updated to: {new_status}

{response_notes if response_notes else ''}

Thank you for choosing FusionFurnish for your custom furniture needs!

Regards,
FusionFurnish Team
"""
    send_email(email, f"FusionFurnish Design Request Update: {new_status}", status_update_email)
    
    return redirect(url_for('admin_design_requests'))

@app.route('/admin/products', methods=['GET'])
@admin_required
def admin_products():
    # Scan for all products
    response = products_table.scan()
    products = response['Items']
    
    return render_template('admin/products.html', products=products)

@app.route('/admin/product/add', methods=['GET', 'POST'])
@admin_required
def add_product():
    if request.method == 'POST':
        product_id = str(uuid.uuid4())
        name = request.form['name']
        category = request.form['category']
        price = Decimal(request.form['price'])
        description = request.form['description']
        stock = int(request.form['stock'])
        image = request.form.get('image', '')
        
        # Add product to DynamoDB
        products_table.put_item(Item={
            'product_id': product_id,
            'name': name,
            'category': category,
            'price': price,
            'description': description,
            'stock': stock,
            'image': image,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        })
        
        return redirect(url_for('admin_products'))
    
    return render_template('admin/add_product.html')

@app.route('/admin/product/edit/<product_id>', methods=['GET', 'POST'])
@admin_required
def edit_product(product_id):
    if request.method == 'POST':
        name = request.form['name']
        category = request.form['category']
        price = Decimal(request.form['price'])
        description = request.form['description']
        stock = int(request.form['stock'])
        image = request.form.get('image', '')
        
        # Update product in DynamoDB
        products_table.update_item(
            Key={'product_id': product_id},
            UpdateExpression='SET #name = :name, category = :category, price = :price, ' + 
                            'description = :description, stock = :stock, image = :image, ' +
                            'updated_at = :updated_at',
            ExpressionAttributeNames={
                '#name': 'name'  # 'name' is a reserved keyword in DynamoDB
            },
            ExpressionAttributeValues={
                ':name': name,
                ':category': category,
                ':price': price,
                ':description': description,
                ':stock': stock,
                ':image': image,
                ':updated_at': datetime.now().isoformat()
            }
        )
        
        return redirect(url_for('admin_products'))
    
    # Get product details
    response = products_table.get_item(Key={'product_id': product_id})
    if 'Item' not in response:
        return "Product not found", 404
    
    product = response['Item']
    
    return render_template('admin/edit_product.html', product=product)

@app.route('/admin/product/delete/<product_id>', methods=['POST'])
@admin_required
def delete_product(product_id):
    # Delete product from DynamoDB
    products_table.delete_item(Key={'product_id': product_id})
    
    return redirect(url_for('admin_products'))

@app.route('/admin/contact-messages')
@admin_required
def admin_contact_messages():
    # Scan for all contact messages
    response = contact_messages_table.scan()
    messages = response['Items']
    
    return render_template('admin/contact_messages.html', messages=messages)

@app.route('/admin/read_message/<message_id>', methods=['POST'])
@admin_required
def read_message(message_id):
    # Update message status to Read
    contact_messages_table.update_item(
        Key={'message_id': message_id},
        UpdateExpression='SET #status = :status',
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': 'Read'
        }
    )
    
    return redirect(url_for('admin_contact_messages'))

@app.route('/admin/reply_message', methods=['POST'])
@admin_required
def reply_message():
    message_id = request.form['message_id']
    reply = request.form['reply']
    
    # Get message details
    response = contact_messages_table.get_item(Key={'message_id': message_id})
    if 'Item' not in response:
        return "Message not found", 404
    
    message = response['Item']
    
    # Send reply email
    reply_email = f"""Hello {message['name']},

Thank you for contacting FusionFurnish. Below is our response to your inquiry:

-----------------
{reply}
-----------------

If you have any further questions, please feel free to contact us.

Regards,
FusionFurnish Team
"""
    send_email(message['email'], f"RE: {message['subject']}", reply_email)
    
    # Update message status
    contact_messages_table.update_item(
        Key={'message_id': message_id},
        UpdateExpression='SET #status = :status, reply = :reply, replied_at = :replied_at',
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': 'Replied',
            ':reply': reply,
            ':replied_at': datetime.now().isoformat()
        }
    )
    
    return redirect(url_for('admin_contact_messages'))

# =========== ERROR HANDLERS ===========

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# =========== MAIN ===========

if __name__ == '__main__':
    app.run(host='0.0.0.0' ,port=5000 ,debug=True)
