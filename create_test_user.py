from app import app, db, Employee

def create_test_user():
    with app.app_context():
        # Create tables if they don't exist
        db.create_all()
        
        # Check if test user already exists
        test_user = Employee.query.filter_by(email='test@example.com').first()
        if test_user:
            print('Test user already exists')
            return
        
        # Create new test user
        test_user = Employee(
            email='test@example.com',
            name='Test User',
            password='password123',
            meal_allowance=100,
            credit_balance=50
        )
        
        try:
            db.session.add(test_user)
            db.session.commit()
            print('Test user created successfully')
        except Exception as e:
            db.session.rollback()
            print(f'Error creating test user: {str(e)}')

if __name__ == '__main__':
    create_test_user()
