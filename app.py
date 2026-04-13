from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os, uuid

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "campus-market-secret-key-2024")
db_path = os.path.join(os.path.dirname(__file__), "instance", "market.db")
os.makedirs(os.path.dirname(db_path), exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", f"sqlite:///{db_path}")
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "请先登录"

# ============ 数据库模型 ============

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    campus = db.Column(db.String(100), default="")
    bio = db.Column(db.String(200), default="")
    avatar = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)
    items = db.relationship("Item", backref="seller", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Float, nullable=False)
    original_price = db.Column(db.Float, default=0)
    category = db.Column(db.String(50), nullable=False)
    condition = db.Column(db.String(20), default="九成新")
    images = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="在售")
    views = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    favorites = db.relationship("Favorite", backref="item", lazy=True, cascade="all, delete-orphan")
    reviews = db.relationship("Review", backref="item", lazy=True, cascade="all, delete-orphan")

    @property
    def favorite_count(self):
        return len(self.favorites)

    @property
    def avg_rating(self):
        if not self.reviews:
            return 0
        return round(sum(r.rating for r in self.reviews) / len(self.reviews), 1)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_read = db.Column(db.Boolean, default=False)
    sender = db.relationship("User", foreign_keys=[sender_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])
    item = db.relationship("Item")

class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    user = db.relationship("User", backref="favorites")

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    buyer = db.relationship("User", foreign_keys=[buyer_id])

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def save_image(file):
    if file and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext in ["jpg", "jpeg", "png", "gif", "webp"]:
            filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            return filename
    return None

CATEGORIES = ["教材书籍", "电子产品", "生活用品", "衣物鞋包", "运动户外", "其他"]

# ============ 首页 ============

@app.route("/")
def index():
    category = request.args.get("category", "")
    search = request.args.get("search", "")
    sort = request.args.get("sort", "newest")
    query = Item.query.filter_by(status="在售")
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Item.title.contains(search) | Item.description.contains(search))
    if sort == "price_asc":
        query = query.order_by(Item.price.asc())
    elif sort == "price_desc":
        query = query.order_by(Item.price.desc())
    elif sort == "popular":
        query = query.order_by(Item.views.desc())
    else:
        query = query.order_by(Item.created_at.desc())
    items = query.all()
    return render_template("index.html", items=items, categories=CATEGORIES,
                           current_category=category, search=search, sort=sort)

# ============ 用户认证 ============

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        campus = request.form.get("campus", "").strip()
        if not username or not phone or not password:
            flash("请填写完整信息")
            return redirect(url_for("register"))
        if len(password) < 6:
            flash("密码至少6位")
            return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("用户名已存在")
            return redirect(url_for("register"))
        if User.query.filter_by(phone=phone).first():
            flash("手机号已注册")
            return redirect(url_for("register"))
        user = User(username=username, phone=phone, campus=campus)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("注册成功！")
        return redirect(url_for("index"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(phone=phone).first()
        if user and user.check_password(password):
            login_user(user)
            flash("登录成功！")
            return redirect(url_for("index"))
        flash("手机号或密码错误")
        return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))

# ============ 商品发布 ============

@app.route("/publish", methods=["GET", "POST"])
@login_required
def publish():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price", 0, type=float)
        original_price = request.form.get("original_price", 0, type=float)
        category = request.form.get("category", "")
        condition = request.form.get("condition", "九成新")
        if not title or not price or not category:
            flash("请填写标题、价格和分类")
            return redirect(url_for("publish"))
        image_names = []
        files = request.files.getlist("images")
        for f in files[:5]:
            name = save_image(f)
            if name:
                image_names.append(name)
        item = Item(title=title, description=description, price=price,
                    original_price=original_price, category=category,
                    condition=condition, images=",".join(image_names),
                    seller_id=current_user.id)
        db.session.add(item)
        db.session.commit()
        flash("发布成功！")
        return redirect(url_for("item_detail", item_id=item.id))
    return render_template("publish.html", categories=CATEGORIES)

# ============ 商品编辑 ============

@app.route("/edit_item/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_item(item_id):
    item = db.session.get(Item, item_id)
    if not item or item.seller_id != current_user.id:
        flash("无权编辑")
        return redirect(url_for("index"))
    if request.method == "POST":
        item.title = request.form.get("title", "").strip()
        item.description = request.form.get("description", "").strip()
        item.price = request.form.get("price", 0, type=float)
        item.original_price = request.form.get("original_price", 0, type=float)
        item.category = request.form.get("category", "")
        item.condition = request.form.get("condition", "九成新")
        # 处理删除旧图片
        keep_images = request.form.getlist("keep_images")
        # 处理新上传的图片
        new_names = []
        files = request.files.getlist("images")
        for f in files[:5]:
            name = save_image(f)
            if name:
                new_names.append(name)
        all_images = keep_images + new_names
        item.images = ",".join(all_images[:5])
        db.session.commit()
        flash("修改成功！")
        return redirect(url_for("item_detail", item_id=item.id))
    return render_template("edit_item.html", item=item, categories=CATEGORIES)

# ============ 商品详情 ============

@app.route("/item/<int:item_id>")
def item_detail(item_id):
    item = db.session.get(Item, item_id)
    if not item:
        flash("商品不存在")
        return redirect(url_for("index"))
    item.views += 1
    db.session.commit()
    is_favorited = False
    if current_user.is_authenticated:
        is_favorited = Favorite.query.filter_by(user_id=current_user.id, item_id=item.id).first() is not None
    reviews = Review.query.filter_by(item_id=item.id).order_by(Review.created_at.desc()).all()
    # 卖家评分
    seller_reviews = Review.query.filter_by(seller_id=item.seller_id).all()
    seller_avg = round(sum(r.rating for r in seller_reviews) / len(seller_reviews), 1) if seller_reviews else 0
    seller_count = len(seller_reviews)
    return render_template("item_detail.html", item=item, is_favorited=is_favorited,
                           reviews=reviews, seller_avg=seller_avg, seller_count=seller_count)

# ============ 收藏功能 ============

@app.route("/toggle_favorite/<int:item_id>", methods=["POST"])
@login_required
def toggle_favorite(item_id):
    item = db.session.get(Item, item_id)
    if not item:
        return jsonify({"error": "商品不存在"}), 404
    fav = Favorite.query.filter_by(user_id=current_user.id, item_id=item_id).first()
    if fav:
        db.session.delete(fav)
        db.session.commit()
        return jsonify({"favorited": False, "count": item.favorite_count})
    else:
        fav = Favorite(user_id=current_user.id, item_id=item_id)
        db.session.add(fav)
        db.session.commit()
        return jsonify({"favorited": True, "count": item.favorite_count})

@app.route("/my_favorites")
@login_required
def my_favorites():
    favs = Favorite.query.filter_by(user_id=current_user.id).order_by(Favorite.created_at.desc()).all()
    items = [f.item for f in favs if f.item and f.item.status == "在售"]
    return render_template("my_favorites.html", items=items)

# ============ 评价系统 ============

@app.route("/review/<int:item_id>", methods=["POST"])
@login_required
def add_review(item_id):
    item = db.session.get(Item, item_id)
    if not item:
        flash("商品不存在")
        return redirect(url_for("index"))
    if item.seller_id == current_user.id:
        flash("不能评价自己的商品")
        return redirect(url_for("item_detail", item_id=item_id))
    existing = Review.query.filter_by(buyer_id=current_user.id, item_id=item_id).first()
    if existing:
        flash("你已经评价过了")
        return redirect(url_for("item_detail", item_id=item_id))
    content = request.form.get("content", "").strip()
    rating = request.form.get("rating", 5, type=int)
    rating = max(1, min(5, rating))
    if not content:
        flash("请填写评价内容")
        return redirect(url_for("item_detail", item_id=item_id))
    review = Review(content=content, rating=rating, buyer_id=current_user.id,
                    item_id=item_id, seller_id=item.seller_id)
    db.session.add(review)
    db.session.commit()
    flash("评价成功！")
    return redirect(url_for("item_detail", item_id=item_id))

# ============ 用户个人主页 ============

@app.route("/user/<int:user_id>")
def user_profile(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("用户不存在")
        return redirect(url_for("index"))
    items = Item.query.filter_by(seller_id=user_id, status="在售").order_by(Item.created_at.desc()).all()
    reviews = Review.query.filter_by(seller_id=user_id).order_by(Review.created_at.desc()).limit(10).all()
    avg_rating = round(sum(r.rating for r in reviews) / len(reviews), 1) if reviews else 0
    total_sold = Item.query.filter_by(seller_id=user_id, status="已售出").count()
    return render_template("user_profile.html", user=user, items=items, reviews=reviews,
                           avg_rating=avg_rating, total_sold=total_sold)

@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        current_user.username = request.form.get("username", "").strip() or current_user.username
        current_user.campus = request.form.get("campus", "").strip()
        current_user.bio = request.form.get("bio", "").strip()
        avatar_file = request.files.get("avatar")
        if avatar_file and avatar_file.filename:
            name = save_image(avatar_file)
            if name:
                current_user.avatar = name
        db.session.commit()
        flash("资料更新成功！")
        return redirect(url_for("user_profile", user_id=current_user.id))
    return render_template("edit_profile.html")

# ============ 聊天 ============

@app.route("/chat/<int:user_id>")
@app.route("/chat/<int:user_id>/<int:item_id>")
@login_required
def chat(user_id, item_id=None):
    other_user = db.session.get(User, user_id)
    if not other_user or other_user.id == current_user.id:
        flash("无法发起对话")
        return redirect(url_for("index"))
    messages = Message.query.filter(
        ((Message.sender_id == current_user.id) & (Message.receiver_id == user_id)) |
        ((Message.sender_id == user_id) & (Message.receiver_id == current_user.id))
    ).order_by(Message.created_at).all()
    Message.query.filter_by(sender_id=user_id, receiver_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    item = db.session.get(Item, item_id) if item_id else None
    return render_template("chat.html", other_user=other_user, messages=messages, item=item)

@app.route("/send_message", methods=["POST"])
@login_required
def send_message():
    content = request.form.get("content", "").strip()
    receiver_id = request.form.get("receiver_id", 0, type=int)
    item_id = request.form.get("item_id", 0, type=int)
    if content and receiver_id:
        msg = Message(content=content, sender_id=current_user.id,
                      receiver_id=receiver_id, item_id=item_id if item_id else None)
        db.session.add(msg)
        db.session.commit()
    return redirect(url_for("chat", user_id=receiver_id, item_id=item_id))

# ============ 我的发布 ============

@app.route("/my_items")
@login_required
def my_items():
    items = Item.query.filter_by(seller_id=current_user.id).order_by(Item.created_at.desc()).all()
    return render_template("my_items.html", items=items)

@app.route("/toggle_status/<int:item_id>")
@login_required
def toggle_status(item_id):
    item = db.session.get(Item, item_id)
    if item and item.seller_id == current_user.id:
        if item.status == "在售":
            item.status = "已下架"
        elif item.status == "已下架":
            item.status = "在售"
        db.session.commit()
    return redirect(url_for("my_items"))

@app.route("/mark_sold/<int:item_id>")
@login_required
def mark_sold(item_id):
    item = db.session.get(Item, item_id)
    if item and item.seller_id == current_user.id:
        item.status = "已售出"
        db.session.commit()
        flash("已标记为已售出")
    return redirect(url_for("my_items"))

@app.route("/delete_item/<int:item_id>")
@login_required
def delete_item(item_id):
    item = db.session.get(Item, item_id)
    if item and item.seller_id == current_user.id:
        db.session.delete(item)
        db.session.commit()
        flash("已删除")
    return redirect(url_for("my_items"))

# ============ 消息列表 ============

@app.route("/conversations")
@login_required
def conversations():
    sent = db.session.query(Message.receiver_id).filter_by(sender_id=current_user.id).distinct().all()
    received = db.session.query(Message.sender_id).filter_by(receiver_id=current_user.id).distinct().all()
    user_ids = set([r[0] for r in sent] + [r[0] for r in received])
    convos = []
    for uid in user_ids:
        user = db.session.get(User, uid)
        last_msg = Message.query.filter(
            ((Message.sender_id == current_user.id) & (Message.receiver_id == uid)) |
            ((Message.sender_id == uid) & (Message.receiver_id == current_user.id))
        ).order_by(Message.created_at.desc()).first()
        unread = Message.query.filter_by(sender_id=uid, receiver_id=current_user.id, is_read=False).count()
        if last_msg:
            convos.append({"user": user, "last_msg": last_msg, "unread": unread})
    convos.sort(key=lambda x: x["last_msg"].created_at, reverse=True)
    return render_template("conversations.html", convos=convos)

# ============ 初始化数据库 ============

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "true").lower() == "true", port=int(os.environ.get("PORT", 5000)))