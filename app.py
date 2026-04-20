from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os, uuid, re, math

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

# ============ 平台配置 ============
COMMISSION_RATE = 0  # 暂不收取提成，先做流量
CATEGORIES = ["教材书籍", "电子产品", "电动车", "四六级专栏", "宿舍好物", "生活用品", "衣物鞋包", "运动户外", "毕业寄卖", "其他"]

# 学号验证：11位数字，前两位为入学年份（20-29）
def is_valid_student_id(sid):
    if not re.match(r'^\d{11}$', sid):
        return False
    year = int(sid[:2])
    return 20 <= year <= 29

def calc_buyer_price(seller_price):
    """计算买家价格（卖家价格 + 平台提成），四舍五入到分"""
    return round(seller_price * (1 + COMMISSION_RATE), 2)

def calc_commission(seller_price):
    """计算平台提成"""
    return round(seller_price * COMMISSION_RATE, 2)

# ============ 数据库模型 ============
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    student_id = db.Column(db.String(20), unique=True, nullable=False)  # 学号
    password_hash = db.Column(db.String(200), nullable=False)
    campus = db.Column(db.String(100), default="")
    bio = db.Column(db.String(200), default="")
    avatar = db.Column(db.String(200), default="")
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)
    ban_reason = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)
    items = db.relationship("Item", backref="seller", lazy=True)

    invite_code = db.Column(db.String(8), unique=True, index=True)
    invited_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    top_quota = db.Column(db.Integer, default=5)
    invite_count = db.Column(db.Integer, default=0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Float, nullable=False)  # 卖家底价
    original_price = db.Column(db.Float, default=0)
    category = db.Column(db.String(50), nullable=False)
    condition = db.Column(db.String(20), default="九成新")
    images = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="在售")
    sale_mode = db.Column(db.String(20), default="普通")
    is_top = db.Column(db.Boolean, default=False)
    views = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    favorites = db.relationship("Favorite", backref="item", lazy=True, cascade="all, delete-orphan")
    reviews = db.relationship("Review", backref="item", lazy=True, cascade="all, delete-orphan")

    @property
    def buyer_price(self):
        return calc_buyer_price(self.price)

    @property
    def commission(self):
        return calc_commission(self.price)

    @property
    def favorite_count(self):
        return len(self.favorites)

    @property
    def avg_rating(self):
        if not self.reviews:
            return 0
        return round(sum(r.rating for r in self.reviews) / len(self.reviews), 1)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(32), unique=True, nullable=False)  # 订单号
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    seller_price = db.Column(db.Float, nullable=False)  # 卖家收到的钱
    buyer_price = db.Column(db.Float, nullable=False)   # 买家付的钱
    commission = db.Column(db.Float, nullable=False)     # 平台提成
    status = db.Column(db.String(20), default="待付款")
    # 交货信息
    pickup_location = db.Column(db.String(200), default="")  # 取货/送货地点
    pickup_time = db.Column(db.String(100), default="")      # 取货时间
    buyer_note = db.Column(db.Text, default="")              # 买家备注
    created_at = db.Column(db.DateTime, default=datetime.now)
    paid_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    buyer = db.relationship("User", foreign_keys=[buyer_id], backref="buy_orders")
    seller = db.relationship("User", foreign_keys=[seller_id], backref="sell_orders")
    item = db.relationship("Item", backref="orders")

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
    user = db.session.get(User, int(user_id))
    if not user:
        user = db.session.get(AIUser, int(user_id))
    return user

def save_image(file):
    if file and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext in ["jpg", "jpeg", "png", "gif", "webp"]:
            filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            return filename
    return None

def generate_order_no():
    return datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:8].upper()

def send_system_message(receiver_id, content):
    """发送系统通知（sender_id=0 表示系统）"""
    # 用管理员账号或创建一个系统用户来发
    admin = User.query.filter_by(is_admin=True).first()
    sender_id = admin.id if admin else 1
    msg = Message(content=content, sender_id=sender_id, receiver_id=receiver_id)
    db.session.add(msg)

def contains_banned_keyword(text):
    """检查文本是否包含屏蔽关键词，返回命中的词或None"""
    if not text:
        return None
    try:
        for kw in BannedKeyword.query.all():
            if kw.keyword in text:
                return kw.keyword
    except Exception:
        pass
    return None

# ============ 首页 ============

@app.before_request
def check_banned_ip():
    from flask import request
    if request.path.startswith('/static'):
        return
    try:
        ip = request.headers.get('X-Real-IP') or request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr
        if ip and BannedIP.query.filter_by(ip=ip).first():
            return "您的IP已被封禁", 403
    except Exception:
        pass

@app.route("/")
def index():
    items = Item.query.filter_by(status="在售").order_by(Item.created_at.desc()).limit(8).all()
    return render_template("home.html", items=items)

@app.route("/market")
def market():
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
        student_id = request.form.get("student_id", "").strip()
        password = request.form.get("password", "")
        campus = request.form.get("campus", "").strip()
        if not username or not student_id or not password:
            flash("请填写完整信息")
            return redirect(url_for("register"))
        if not is_valid_student_id(student_id):
            flash("学号格式不正确（应为11位数字，如24301201005）")
            return redirect(url_for("register"))
        if len(password) < 6:
            flash("密码至少6位")
            return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("用户名已存在")
            return redirect(url_for("register"))
        if User.query.filter_by(student_id=student_id).first():
            flash("该学号已注册")
            return redirect(url_for("register"))
        # 生成唯一邀请码
        import string, random
        def gen_invite_code():
            while True:
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                if not User.query.filter_by(invite_code=code).first():
                    return code

        user = User(username=username, student_id=student_id, campus=campus, invite_code=gen_invite_code(), top_quota=5)
        user.set_password(password)

        # 处理邀请码
        invite_code_input = request.form.get("invite_code", "").strip().upper()
        if invite_code_input:
            inviter = User.query.filter_by(invite_code=invite_code_input).first()
            if inviter:
                user.invited_by = inviter.id
                user.top_quota = 10  # 被邀请人 5 基础 + 5 奖励
                inviter.top_quota = (inviter.top_quota or 0) + 3
                inviter.invite_count = (inviter.invite_count or 0) + 1
            else:
                flash("邀请码无效（已忽略，不影响注册）")

        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("注册成功！" + ("通过邀请码注册，已获得 10 次顶级模型额度 🎁" if invite_code_input and user.invited_by else "已获得 5 次顶级模型体验额度 🎁"))
        return redirect(url_for("index"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(student_id=student_id).first()
        if user and user.check_password(password):
            login_user(user)
            flash("登录成功！")
            return redirect(url_for("index"))
        flash("学号或密码错误")
        return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))

# ============ 商品发布 ============

@app.route("/sell-book", methods=["GET","POST"])
@login_required
def sell_book():
    if request.method == "POST":
        title = request.form.get("title","").strip()
        desc = request.form.get("description","").strip()
        price = float(request.form.get("price",0))
        orig = request.form.get("original_price","")
        cond = request.form.get("condition","九成新")
        if not title:
            flash("请填写书名")
            return redirect(url_for("sell_book"))
        item = Item(title=title, description=desc, price=price,
                    original_price=float(orig) if orig else None,
                    category="教材书籍", condition=cond,
                    seller_id=current_user.id, sale_mode="卖书")
        images = request.files.getlist("images")
        fnames = []
        for img in images[:5]:
            if img and img.filename:
                fn = f"{int(__import__('time').time()*1000)}_{img.filename}"
                img.save(os.path.join(app.config["UPLOAD_FOLDER"], fn))
                fnames.append(fn)
        if fnames:
            item.images = ",".join(fnames)
        db.session.add(item)
        db.session.commit()
        flash("书籍发布成功！")
        return redirect(url_for("market"))
    return render_template("sell_book.html")

@app.route("/consign", methods=["GET","POST"])
@login_required
def consign():
    if request.method == "POST":
        title = request.form.get("title","").strip()
        desc = request.form.get("description","").strip()
        price = float(request.form.get("price",0))
        orig = request.form.get("original_price","")
        cond = request.form.get("condition","九成新")
        consign_cat = request.form.get("consign_cat","")
        contact = request.form.get("contact","").strip()
        if not title:
            flash("请填写商品名称")
            return redirect(url_for("consign"))
        if price < 300:
            flash("寄卖商品售价需≥300元")
            return redirect(url_for("consign"))
        if not consign_cat:
            flash("请选择寄卖品类")
            return redirect(url_for("consign"))
        fee = round(price * 0.3)
        item = Item(title=f"[寄卖] {title}", description=f"{desc}\n\n---\n寄卖品类：{consign_cat}\n联系方式：{contact}\n手续费：{fee}元（30%）",
                    price=price, buyer_price=price,
                    original_price=float(orig) if orig else None,
                    category="毕业寄卖", condition=cond,
                    seller_id=current_user.id, sale_mode="寄卖")
        images = request.files.getlist("images")
        fnames = []
        for img in images[:5]:
            if img and img.filename:
                fn = f"{int(__import__('time').time()*1000)}_{img.filename}"
                img.save(os.path.join(app.config["UPLOAD_FOLDER"], fn))
                fnames.append(fn)
        if fnames:
            item.images = ",".join(fnames)
        db.session.add(item)
        db.session.commit()
        flash("寄卖提交成功！商品已上架")
        return redirect(url_for("market"))
    return render_template("consign.html")

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
        keep_images = request.form.getlist("keep_images")
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
    seller_reviews = Review.query.filter_by(seller_id=item.seller_id).all()
    seller_avg = round(sum(r.rating for r in seller_reviews) / len(seller_reviews), 1) if seller_reviews else 0
    seller_count = len(seller_reviews)
    return render_template("item_detail.html", item=item, is_favorited=is_favorited,
                           reviews=reviews, seller_avg=seller_avg, seller_count=seller_count)

# ============ 订单系统 ============

@app.route("/create_order/<int:item_id>", methods=["GET", "POST"])
@login_required
def create_order(item_id):
    item = db.session.get(Item, item_id)
    if not item or item.status != "在售":
        flash("商品不存在或已下架")
        return redirect(url_for("index"))
    if item.seller_id == current_user.id:
        flash("不能购买自己的商品")
        return redirect(url_for("item_detail", item_id=item_id))
    if request.method == "POST":
        pickup_location = request.form.get("pickup_location", "").strip()
        pickup_time = request.form.get("pickup_time", "").strip()
        buyer_note = request.form.get("buyer_note", "").strip()
        if not pickup_location or not pickup_time:
            flash("请填写取货地点和时间")
            return redirect(url_for("create_order", item_id=item_id))
        order = Order(
            order_no=generate_order_no(),
            buyer_id=current_user.id,
            seller_id=item.seller_id,
            item_id=item.id,
            seller_price=item.price,
            buyer_price=item.buyer_price,
            commission=item.commission,
            pickup_location=pickup_location,
            pickup_time=pickup_time,
            buyer_note=buyer_note,
            status="待交易"
        )
        db.session.add(order)
        item.status = "已锁定"
        send_system_message(
            item.seller_id,
            f"【新订单】你的商品「{item.title}」有新买家下单了！\n"
            f"请在约定时间前往交货地点完成交易。\n"
            f"地点：{pickup_location}\n"
            f"时间：{pickup_time}\n"
            f"价格：¥{item.price}\n"
            f"⚠️ 请当面一手交钱一手交货。"
        )
        send_system_message(
            current_user.id,
            f"【下单成功】你已预约购买「{item.title}」\n"
            f"请在约定时间前往交货地点完成交易。\n"
            f"地点：{pickup_location}\n"
            f"时间：{pickup_time}\n"
            f"价格：¥{item.price}\n"
            f"⚠️ 请当面一手交钱一手交货。"
        )
        db.session.commit()
        flash("下单成功！请尽快完成付款。")
        return redirect(url_for("order_detail", order_id=order.id))
    return render_template("create_order.html", item=item)

@app.route("/order/<int:order_id>")
@login_required
def order_detail(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        flash("订单不存在")
        return redirect(url_for("index"))
    if order.buyer_id != current_user.id and order.seller_id != current_user.id and not current_user.is_admin:
        flash("无权查看")
        return redirect(url_for("index"))
    return render_template("order_detail.html", order=order)

@app.route("/confirm_payment/<int:order_id>", methods=["POST"])
@login_required
def confirm_payment(order_id):
    """买家确认已付款（后续接入支付API后改为自动）"""
    order = db.session.get(Order, order_id)
    if not order or order.buyer_id != current_user.id or order.status != "待付款":
        flash("操作无效")
        return redirect(url_for("my_orders"))
    order.status = "待发货"
    order.paid_at = datetime.now()
    send_system_message(
        order.seller_id,
        f"【买家已付款】订单「{order.item.title}」买家已确认付款。\n"
        f"请在约定时间将商品送到：{order.pickup_location}"
    )
    db.session.commit()
    flash("已确认付款，等待卖家发货")
    return redirect(url_for("order_detail", order_id=order.id))

@app.route("/confirm_delivery/<int:order_id>", methods=["POST"])
@login_required
def confirm_delivery(order_id):
    """卖家确认已送货"""
    order = db.session.get(Order, order_id)
    if not order or order.seller_id != current_user.id or order.status != "待发货":
        flash("操作无效")
        return redirect(url_for("my_sales"))
    order.status = "待收货"
    send_system_message(
        order.buyer_id,
        f"【卖家已送货】订单「{order.item.title}」卖家已将商品送到：{order.pickup_location}\n"
        f"请前往取货并确认收货。"
    )
    db.session.commit()
    flash("已确认送货")
    return redirect(url_for("order_detail", order_id=order.id))

@app.route("/confirm_received/<int:order_id>", methods=["POST"])
@login_required
def confirm_received(order_id):
    """买家确认收货"""
    order = db.session.get(Order, order_id)
    if not order or order.buyer_id != current_user.id or order.status != "待收货":
        flash("操作无效")
        return redirect(url_for("my_orders"))
    order.status = "已完成"
    order.completed_at = datetime.now()
    order.item.status = "已售出"
    send_system_message(
        order.seller_id,
        f"【交易完成】订单「{order.item.title}」买家已确认收货，交易完成！\n"
        f"你将收到：¥{order.seller_price}"
    )
    db.session.commit()
    flash("已确认收货，交易完成！")
    return redirect(url_for("order_detail", order_id=order.id))


@app.route("/update_order_info/<int:order_id>", methods=["POST"])
@login_required
def update_order_info(order_id):
    order = db.session.get(Order, order_id)
    if not order or order.status in ["已完成", "已取消"]:
        flash("无法修改")
        return redirect(url_for("my_orders"))
    if order.buyer_id != current_user.id and order.seller_id != current_user.id:
        flash("无权操作")
        return redirect(url_for("index"))
    new_location = request.form.get("pickup_location", "").strip()
    new_time = request.form.get("pickup_time", "").strip()
    if not new_location or not new_time:
        flash("请填写完整信息")
        return redirect(url_for("order_detail", order_id=order.id))
    old_location = order.pickup_location
    old_time = order.pickup_time
    order.pickup_location = new_location
    order.pickup_time = new_time
    other_id = order.seller_id if current_user.id == order.buyer_id else order.buyer_id
    role_name = "买家" if current_user.id == order.buyer_id else "卖家"
    send_system_message(
        other_id,
        f"【交货信息变更】{role_name}修改了订单「{order.item.title}」的交货信息：\n"
        f"地点：{old_location} → {new_location}\n"
        f"时间：{old_time} → {new_time}\n"
        f"如有问题请及时沟通。"
    )
    db.session.commit()
    flash("交货信息已更新，已通知对方")
    return redirect(url_for("order_detail", order_id=order.id))

@app.route("/cancel_order/<int:order_id>", methods=["POST"])
@login_required
def cancel_order(order_id):
    """取消订单"""
    order = db.session.get(Order, order_id)
    if not order:
        flash("订单不存在")
        return redirect(url_for("my_orders"))
    # 买家在付款前可以取消，卖家也可以取消
    if order.status not in ["待付款", "待发货"]:
        flash("当前状态无法取消")
        return redirect(url_for("order_detail", order_id=order.id))
    if order.buyer_id != current_user.id and order.seller_id != current_user.id:
        flash("无权操作")
        return redirect(url_for("index"))
    order.status = "已取消"
    order.item.status = "在售"
    other_id = order.seller_id if current_user.id == order.buyer_id else order.buyer_id
    send_system_message(
        other_id,
        f"【订单取消】订单「{order.item.title}」已被对方取消。"
    )
    db.session.commit()
    flash("订单已取消")
    return redirect(url_for("order_detail", order_id=order.id))

@app.route("/my_orders")
@login_required
def my_orders():
    orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("my_orders.html", orders=orders, role="buyer")

@app.route("/my_sales")
@login_required
def my_sales():
    orders = Order.query.filter_by(seller_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("my_orders.html", orders=orders, role="seller")

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

# ============ 用户主页 ============

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

@app.route("/change_password", methods=["POST"])
@login_required
def change_password():
    old_pwd = request.form.get("old_password","")
    new_pwd = request.form.get("new_password","")
    confirm = request.form.get("confirm_password","")
    if not current_user.check_password(old_pwd):
        flash("当前密码错误", "pwd")
    elif len(new_pwd) < 6:
        flash("新密码至少6位", "pwd")
    elif new_pwd != confirm:
        flash("两次输入的新密码不一致", "pwd")
    else:
        current_user.set_password(new_pwd)
        db.session.commit()
        flash("密码已修改，请妥善保管", "pwd")
    return redirect(url_for("edit_profile"))

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


# ============ AI 导师 ============

@app.route("/tutor")
def tutor():
    return render_template("tutor.html")

# ============ 管理后台 ============

@app.route("/admin")
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash("无权访问")
        return redirect(url_for("index"))
    total_users = User.query.count()
    total_items = Item.query.count()
    items_on_sale = Item.query.filter_by(status="在售").count()
    total_orders = Order.query.count()
    completed_orders = Order.query.filter_by(status="已完成").count()
    total_revenue = db.session.query(db.func.sum(Order.commission)).filter_by(status="已完成").scalar() or 0
    total_gmv = db.session.query(db.func.sum(Order.buyer_price)).filter_by(status="已完成").scalar() or 0
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(20).all()
    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()
    return render_template("admin.html",
                           total_users=total_users, total_items=total_items,
                           items_on_sale=items_on_sale, total_orders=total_orders,
                           completed_orders=completed_orders, total_revenue=round(total_revenue, 2),
                           total_gmv=round(total_gmv, 2), recent_orders=recent_orders,
                           recent_users=recent_users, commission_rate=COMMISSION_RATE * 100)

# ==== 商品管理 ====
@app.route("/admin/items")
@login_required
def admin_items():
    if not current_user.is_admin: return redirect(url_for("index"))
    q = request.args.get("q","")
    mode = request.args.get("mode","")
    query = Item.query
    if q: query = query.filter(Item.title.contains(q))
    if mode: query = query.filter_by(sale_mode=mode)
    items = query.order_by(Item.created_at.desc()).limit(100).all()
    return render_template("admin_items.html", items=items, q=q, mode=mode)

@app.route("/admin/item/<int:item_id>/delete", methods=["POST"])
@login_required
def admin_del_item(item_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    item = Item.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash(f"已删除商品：{item.title}")
    return redirect(url_for("admin_items"))

@app.route("/admin/item/<int:item_id>/top", methods=["POST"])
@login_required
def admin_top_item(item_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    item = Item.query.get_or_404(item_id)
    item.is_top = not item.is_top
    db.session.commit()
    flash(f"{'已置顶' if item.is_top else '已取消置顶'}：{item.title}")
    return redirect(url_for("admin_items"))

# ==== 用户管理 ====
@app.route("/admin/users")
@login_required
def admin_users():
    if not current_user.is_admin: return redirect(url_for("index"))
    q = request.args.get("q","")
    query = User.query
    if q: query = query.filter(db.or_(User.username.contains(q), User.student_id.contains(q)))
    users = query.order_by(User.created_at.desc()).limit(200).all()
    return render_template("admin_users.html", users=users, q=q)

@app.route("/admin/user/<int:user_id>/ban", methods=["POST"])
@login_required
def admin_ban_user(user_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash("不能封禁管理员")
        return redirect(url_for("admin_users"))
    user.is_banned = not user.is_banned
    user.ban_reason = request.form.get("reason","") if user.is_banned else ""
    db.session.commit()
    flash(f"{'已封禁' if user.is_banned else '已解封'}：{user.username}")
    return redirect(url_for("admin_users"))

@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_del_user(user_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash("不能删除管理员")
        return redirect(url_for("admin_users"))
    db.session.delete(user)
    db.session.commit()
    flash(f"已删除用户：{user.username}")
    return redirect(url_for("admin_users"))

# ==== 找搭子帖子管理 ====
@app.route("/admin/buddy")
@login_required
def admin_buddy():
    if not current_user.is_admin: return redirect(url_for("index"))
    posts = BuddyPost.query.order_by(BuddyPost.created_at.desc()).limit(100).all()
    return render_template("admin_buddy.html", posts=posts)

@app.route("/admin/buddy/<int:post_id>/delete", methods=["POST"])
@login_required
def admin_del_buddy(post_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    post = BuddyPost.query.get_or_404(post_id)
    db.session.delete(post)
    db.session.commit()
    flash("帖子已删除")
    return redirect(url_for("admin_buddy"))

# ==== 寄卖订单管理 ====
@app.route("/admin/consign")
@login_required
def admin_consign():
    if not current_user.is_admin: return redirect(url_for("index"))
    items = Item.query.filter_by(sale_mode="寄卖").order_by(Item.created_at.desc()).all()
    total_fee = sum(item.price * 0.3 for item in items if item.status == "已售")
    return render_template("admin_consign.html", items=items, total_fee=round(total_fee, 2))

# ==== 公告管理 ====
@app.route("/admin/announcements", methods=["GET","POST"])
@login_required
def admin_announcements():
    if not current_user.is_admin: return redirect(url_for("index"))
    if request.method == "POST":
        title = request.form.get("title","").strip()
        content = request.form.get("content","").strip()
        atype = request.form.get("type","通知")
        if title and content:
            ann = Announcement(title=title, content=content, type=atype, created_by=current_user.id)
            db.session.add(ann)
            db.session.commit()
            flash("公告已发布")
        return redirect(url_for("admin_announcements"))
    anns = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template("admin_announcements.html", anns=anns)

@app.route("/admin/announcement/<int:ann_id>/toggle", methods=["POST"])
@login_required
def admin_toggle_ann(ann_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    ann = Announcement.query.get_or_404(ann_id)
    ann.is_active = not ann.is_active
    db.session.commit()
    return redirect(url_for("admin_announcements"))

@app.route("/admin/announcement/<int:ann_id>/delete", methods=["POST"])
@login_required
def admin_del_ann(ann_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    ann = Announcement.query.get_or_404(ann_id)
    db.session.delete(ann)
    db.session.commit()
    flash("公告已删除")
    return redirect(url_for("admin_announcements"))

# ==== 修改管理员密码 ====
@app.route("/admin/password", methods=["GET","POST"])
@login_required
def admin_password():
    if not current_user.is_admin: return redirect(url_for("index"))
    if request.method == "POST":
        old_pwd = request.form.get("old_password","")
        new_pwd = request.form.get("new_password","")
        if not current_user.check_password(old_pwd):
            flash("原密码错误")
        elif len(new_pwd) < 6:
            flash("新密码至少6位")
        else:
            current_user.set_password(new_pwd)
            db.session.commit()
            flash("密码已修改")
        return redirect(url_for("admin_password"))
    return render_template("admin_password.html")

# ==== 关键词管理 ====
@app.route("/admin/keywords", methods=["GET","POST"])
@login_required
def admin_keywords():
    if not current_user.is_admin: return redirect(url_for("index"))
    if request.method == "POST":
        kw = request.form.get("keyword","").strip()
        if kw and not BannedKeyword.query.filter_by(keyword=kw).first():
            db.session.add(BannedKeyword(keyword=kw))
            db.session.commit()
            flash(f"已添加屏蔽词：{kw}")
        return redirect(url_for("admin_keywords"))
    kws = BannedKeyword.query.order_by(BannedKeyword.created_at.desc()).all()
    return render_template("admin_keywords.html", kws=kws)

@app.route("/admin/keyword/<int:kw_id>/delete", methods=["POST"])
@login_required
def admin_del_kw(kw_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    kw = BannedKeyword.query.get_or_404(kw_id)
    db.session.delete(kw)
    db.session.commit()
    return redirect(url_for("admin_keywords"))

# ==== IP黑名单 ====
@app.route("/admin/ips", methods=["GET","POST"])
@login_required
def admin_ips():
    if not current_user.is_admin: return redirect(url_for("index"))
    if request.method == "POST":
        ip = request.form.get("ip","").strip()
        reason = request.form.get("reason","").strip()
        if ip and not BannedIP.query.filter_by(ip=ip).first():
            db.session.add(BannedIP(ip=ip, reason=reason))
            db.session.commit()
            flash(f"已封禁IP：{ip}")
        return redirect(url_for("admin_ips"))
    ips = BannedIP.query.order_by(BannedIP.created_at.desc()).all()
    return render_template("admin_ips.html", ips=ips)

@app.route("/admin/ip/<int:ip_id>/delete", methods=["POST"])
@login_required
def admin_del_ip(ip_id):
    if not current_user.is_admin: return redirect(url_for("index"))
    ip = BannedIP.query.get_or_404(ip_id)
    db.session.delete(ip)
    db.session.commit()
    return redirect(url_for("admin_ips"))

# ==== 数据导出 ====
@app.route("/admin/export/<kind>")
@login_required
def admin_export(kind):
    if not current_user.is_admin: return redirect(url_for("index"))
    import csv, io
    si = io.StringIO()
    si.write("\ufeff")  # BOM for Excel中文
    cw = csv.writer(si)
    if kind == "users":
        cw.writerow(["ID","用户名","学号","校区","注册时间","是否封禁"])
        for u in User.query.all():
            cw.writerow([u.id, u.username, u.student_id, u.campus or "", u.created_at.strftime("%Y-%m-%d %H:%M"), "是" if u.is_banned else "否"])
        fn = "users.csv"
    elif kind == "orders":
        cw.writerow(["订单号","商品","买家","卖家","金额","佣金","状态","时间"])
        for o in Order.query.all():
            cw.writerow([o.order_no, o.item.title if o.item else "", o.buyer.username if o.buyer else "", o.seller.username if o.seller else "", o.buyer_price, o.commission, o.status, o.created_at.strftime("%Y-%m-%d %H:%M")])
        fn = "orders.csv"
    else:
        return "invalid"
    from flask import Response
    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename={fn}"})

# ==== AI使用统计 ====
@app.route("/admin/ai-stats")
@login_required
def admin_ai_stats():
    if not current_user.is_admin: return redirect(url_for("index"))
    try:
        ai_users = AIUser.query.count()
        daily_usages = DailyUsage.query.order_by(DailyUsage.date.desc()).limit(100).all()
        return render_template("admin_ai_stats.html", ai_users=ai_users, daily_usages=daily_usages)
    except Exception as e:
        flash(f"AI统计模块未启用: {e}")
        return redirect(url_for("admin_dashboard"))


# ============ 多模型 AI 代理 ============

AI_KEYS = {
    'glm': 'beecbac7fa344579bf021caf004cc6c3.pYkyMoSpoqmuMFx6',
    'kimi': os.environ.get('KIMI_API_KEY', ''),
    'deepseek': os.environ.get('DEEPSEEK_API_KEY', ''),
}

AI_MODELS = {
    'top':   {'provider':'glm',      'model':'glm-4-plus',       'url':'https://open.bigmodel.cn/api/paas/v4/chat/completions',  'name':'💎 顶级导师', 'desc':'GLM-4-Plus · 推理最强'},
    'smart': {'provider':'kimi',     'model':'moonshot-v1-32k',  'url':'https://api.moonshot.cn/v1/chat/completions',            'name':'🚀 学霸导师', 'desc':'Kimi K2 · 中文长文本'},
    'fast':  {'provider':'deepseek', 'model':'deepseek-chat',    'url':'https://api.deepseek.com/v1/chat/completions',           'name':'⚡ 快速导师', 'desc':'DeepSeek · 秒级响应'},
}

# 各任务默认模型
TASK_MODEL_MAP = {
    'mindmap': 'smart',    # Kimi 中文结构化最好
    'quiz':    'top',      # GLM 出题精准
    'chat':    'top',      # GLM 答疑深入
    'example': 'top',      # GLM 举一反三
    'bili':    'smart',    # Kimi 长文本
    'default': 'fast',
}

@app.route("/api/ai", methods=["POST"])
def ai_proxy():
    """统一 AI 代理：前端调用这个，后端转发到对应模型。保护 API Key 不暴露。"""
    import requests as http_req
    from flask import Response, stream_with_context
    data = request.get_json() or {}
    
    # 获取用户选择的档位，或按任务自动选
    tier = data.get('tier') or TASK_MODEL_MAP.get(data.get('task','default'), 'fast')
    if tier not in AI_MODELS:
        tier = 'fast'
    
    # 游客限制：未登录只能用 fast 档，每IP每天限3次
    if not current_user.is_authenticated:
        if tier != 'fast':
            tier = 'fast'
        from datetime import date
        ip = request.headers.get('X-Real-IP') or request.remote_addr or 'unknown'
        key = ip + '_' + date.today().isoformat()
        if not hasattr(app, '_guest_usage'):
            app._guest_usage = {}
        app._guest_usage[key] = app._guest_usage.get(key, 0) + 1
        if app._guest_usage[key] > 3:
            return {'error': 'guest_limit', 'message': '游客每天可免费体验3次，登录后不限次使用'}, 429
    # 登录用户：顶级模型按 top_quota 扣费，不足时降级到学霸
    elif tier == 'top':
        if (current_user.top_quota or 0) > 0:
            current_user.top_quota = current_user.top_quota - 1
            db.session.commit()
        else:
            tier = 'smart'  # 额度不足，降级到学霸模型
    
    cfg = AI_MODELS[tier]
    api_key = AI_KEYS[cfg['provider']]
    
    # 构建转发请求体
    payload = {
        'model': cfg['model'],
        'messages': data.get('messages', []),
        'max_tokens': data.get('max_tokens', 4000),
        'temperature': data.get('temperature', 0.7),
    }
    stream = data.get('stream', False)
    if stream:
        payload['stream'] = True
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    
    try:
        if stream:
            # 流式转发
            def generate():
                r = http_req.post(cfg['url'], headers=headers, json=payload, timeout=60, stream=True)
                for chunk in r.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
            return Response(stream_with_context(generate()), mimetype='text/event-stream')
        else:
            r = http_req.post(cfg['url'], headers=headers, json=payload, timeout=60)
            return Response(r.content, status=r.status_code, mimetype='application/json')
    except Exception as e:
        return {'error': str(e)}, 500

@app.route("/api/ai/models")
def ai_model_list():
    """返回可用模型列表给前端"""
    return {tier: {'name':cfg['name'], 'desc':cfg['desc']} for tier, cfg in AI_MODELS.items()}

# ============ 初始化数据库 ============



# ===== 错题本 =====


# ===== 找搭子功能 =====

class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(20), default="通知")  # 通知/活动/维护
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))

class BannedKeyword(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(100), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

class BannedIP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50), nullable=False, unique=True)
    reason = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

# Item加置顶字段
BUDDY_CATEGORIES = ['学习搭子','运动搭子','饭搭子','游戏搭子','麻将搭子','羽毛球搭子','篮球搭子','跑步搭子','其他']

class BuddyPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    time_info = db.Column(db.String(100))  # 时间要求，如"周末下午"
    location = db.Column(db.String(100))   # 地点
    max_people = db.Column(db.Integer, default=1)  # 需要几个搭子
    status = db.Column(db.String(10), default="招募中")  # 招募中/已满员/已关闭
    created_at = db.Column(db.DateTime, default=datetime.now)
    user = db.relationship("User", backref="buddy_posts")

class BuddyMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("buddy_post.id"), nullable=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_read = db.Column(db.Boolean, default=False)
    sender = db.relationship("User", foreign_keys=[sender_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])
    post = db.relationship("BuddyPost")

class WechatExchange(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    target_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    requester_wechat = db.Column(db.String(50))
    target_wechat = db.Column(db.String(50))
    status = db.Column(db.String(10), default="pending")  # pending/accepted/rejected
    created_at = db.Column(db.DateTime, default=datetime.now)
    requester = db.relationship("User", foreign_keys=[requester_id])
    target = db.relationship("User", foreign_keys=[target_id])

# ===== AI独立站用户 =====
class AIUser(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(50), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.now())

class WrongAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question = db.Column(db.Text, nullable=False)
    correct_answer = db.Column(db.Text, nullable=False)
    user_answer = db.Column(db.Text, default='')
    explanation = db.Column(db.Text, default='')
    subject = db.Column(db.String(100), default='')
    source_title = db.Column(db.String(200), default='')
    created_at = db.Column(db.DateTime, default=db.func.now())
    review_count = db.Column(db.Integer, default=0)
    mastered = db.Column(db.Boolean, default=False)

# ===== 学习记录 =====
class StudyLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)
    mindmap_count = db.Column(db.Integer, default=0)
    quiz_count = db.Column(db.Integer, default=0)
    chat_count = db.Column(db.Integer, default=0)
    wrong_count = db.Column(db.Integer, default=0)
    study_minutes = db.Column(db.Integer, default=0)

    __table_args__ = (db.UniqueConstraint('user_id', 'date'),)


class DailyUsage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    feature = db.Column(db.String(20), nullable=False)  # mindmap/quiz/chat/example
    count = db.Column(db.Integer, default=0)

    __table_args__ = (db.UniqueConstraint('user_id', 'date', 'feature'),)

with app.app_context():
    db.create_all()
    # 自动创建管理员账号（学号 00000000000）
    if not User.query.filter_by(is_admin=True).first():
        admin = User(username="管理员", student_id="00000000000", is_admin=True)
        admin.set_password("admin123456")
        db.session.add(admin)
        db.session.commit()


# ===== 每日使用限制 =====
DAILY_LIMITS = {
    'mindmap': 3,
    'quiz': 3,
    'chat': 10,
    'example': 2
}

@app.route('/api/check_usage/<feature>')
@login_required
def check_usage(feature):
    from datetime import date
    if feature not in DAILY_LIMITS:
        return jsonify({'error': 'invalid feature'}), 400
    today = date.today().isoformat()
    usage = DailyUsage.query.filter_by(user_id=current_user.id, date=today, feature=feature).first()
    used = usage.count if usage else 0
    limit = DAILY_LIMITS[feature]
    return jsonify({'used': used, 'limit': limit, 'remaining': limit - used})

@app.route('/api/record_usage/<feature>', methods=['POST'])

def record_usage(feature):
    from datetime import date
    if feature not in DAILY_LIMITS:
        return jsonify({'error': 'invalid feature'}), 400
    # 管理员无限使用
    if getattr(current_user, 'is_admin', False):
        return jsonify({'allowed': True, 'remaining': 999})
    today = date.today().isoformat()
    usage = DailyUsage.query.filter_by(user_id=current_user.id, date=today, feature=feature).first()
    if not usage:
        usage = DailyUsage(user_id=current_user.id, date=today, feature=feature, count=0)
        db.session.add(usage)
    limit = DAILY_LIMITS[feature]
    if usage.count >= limit:
        return jsonify({'allowed': False, 'msg': f'今日{feature}已达上限({limit}次)，明天再来吧~'})
    usage.count += 1
    db.session.commit()
    return jsonify({'allowed': True, 'remaining': limit - usage.count})


# ===== B站视频字幕提取 =====
import requests as http_requests
import re as re_module

@app.route('/api/bili_extract', methods=['POST'])
@login_required
def bili_extract():
    data = request.get_json()
    url = data.get('url', '')

    bv_match = re_module.search(r'(BV[a-zA-Z0-9]+)', url)
    if not bv_match:
        return jsonify({'error': '无法识别B站视频链接'}), 400

    bvid = bv_match.group(1)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.bilibili.com'
    }

    try:
        # 获取视频基本信息
        info_res = http_requests.get(
            f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}',
            headers=headers, timeout=10
        ).json()

        if info_res.get('code') != 0:
            return jsonify({'error': '视频不存在或无法访问'}), 400

        vdata = info_res['data']
        title = vdata.get('title', '')
        desc = vdata.get('desc', '')
        cid = vdata.get('cid', 0)
        owner = vdata.get('owner', {}).get('name', '')

        # 获取标签
        tags_text = ''
        try:
            tags_res = http_requests.get(
                f'https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}',
                headers=headers, timeout=5
            ).json()
            if tags_res.get('code') == 0:
                tags_text = '\u3001'.join(
                    [t.get('tag_name', '') for t in tags_res.get('data', [])][:10]
                )
        except:
            pass

        # 获取字幕URL（通过弹幕接口，不需要登录）
        subtitle_url = ''
        try:
            dm_res = http_requests.get(
                f'https://api.bilibili.com/x/v2/dm/view?oid={cid}&type=1',
                headers=headers, timeout=10
            ).json()
            subs = dm_res.get('data', {}).get('subtitle', {}).get('subtitles', [])
            if subs:
                # 优先中文字幕
                for sub in subs:
                    if 'zh' in sub.get('lan', ''):
                        subtitle_url = sub.get('subtitle_url', '')
                        break
                if not subtitle_url:
                    subtitle_url = subs[0].get('subtitle_url', '')
                if subtitle_url and subtitle_url.startswith('//'):
                    subtitle_url = 'https:' + subtitle_url
                elif subtitle_url and subtitle_url.startswith('http://'):
                    subtitle_url = 'https://' + subtitle_url[7:]
        except:
            pass

        result_text = f"\u3010\u89c6\u9891\u6807\u9898\u3011{title}\n"
        result_text += f"\u3010UP\u4e3b\u3011{owner}\n"
        if tags_text:
            result_text += f"\u3010\u6807\u7b7e\u3011{tags_text}\n"
        if desc:
            result_text += f"\u3010\u89c6\u9891\u7b80\u4ecb\u3011{desc}\n"

        return jsonify({
            'success': True,
            'title': title,
            'content': result_text,
            'subtitle_url': subtitle_url,
            'has_subtitle': bool(subtitle_url),
            'bvid': bvid
        })

    except Exception as e:
        return jsonify({'error': f'提取失败：{str(e)}'}), 500


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "true").lower() == "true", port=int(os.environ.get("PORT", 5000)))


# ===== 错题本API =====
@app.route('/api/wrong_answers', methods=['POST'])
@login_required
def save_wrong_answer():
    data = request.get_json()
    wa = WrongAnswer(
        user_id=current_user.id,
        question=data.get('question', ''),
        correct_answer=data.get('correct_answer', ''),
        user_answer=data.get('user_answer', ''),
        explanation=data.get('explanation', ''),
        subject=data.get('subject', ''),
        source_title=data.get('source_title', '')
    )
    db.session.add(wa)
    db.session.commit()
    return jsonify({'success': True, 'id': wa.id})

@app.route('/api/wrong_answers', methods=['GET'])
@login_required
def get_wrong_answers():
    page = request.args.get('page', 1, type=int)
    mastered = request.args.get('mastered', 'false') == 'true'
    query = WrongAnswer.query.filter_by(user_id=current_user.id, mastered=mastered)
    total = query.count()
    items = query.order_by(WrongAnswer.created_at.desc()).offset((page-1)*20).limit(20).all()
    return jsonify({
        'total': total,
        'items': [{
            'id': w.id,
            'question': w.question,
            'correct_answer': w.correct_answer,
            'user_answer': w.user_answer,
            'explanation': w.explanation,
            'subject': w.subject,
            'source_title': w.source_title,
            'created_at': w.created_at.strftime('%Y-%m-%d %H:%M') if w.created_at else '',
            'review_count': w.review_count,
            'mastered': w.mastered
        } for w in items]
    })

@app.route('/api/wrong_answers/<int:wid>/master', methods=['POST'])
@login_required
def master_wrong_answer(wid):
    wa = WrongAnswer.query.get_or_404(wid)
    if wa.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    wa.mastered = not wa.mastered
    db.session.commit()
    return jsonify({'success': True, 'mastered': wa.mastered})

@app.route('/api/wrong_answers/<int:wid>', methods=['DELETE'])
@login_required
def delete_wrong_answer(wid):
    wa = WrongAnswer.query.get_or_404(wid)
    if wa.user_id != current_user.id:
        return jsonify({'error': 'forbidden'}), 403
    db.session.delete(wa)
    db.session.commit()
    return jsonify({'success': True})

# ===== 学习数据面板API =====
@app.route('/api/study_stats', methods=['GET'])
@login_required
def get_study_stats():
    from datetime import datetime, timedelta
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 总错题数
    total_wrong = WrongAnswer.query.filter_by(user_id=current_user.id, mastered=False).count()
    mastered_wrong = WrongAnswer.query.filter_by(user_id=current_user.id, mastered=True).count()
    
    # 最近7天的使用记录
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    recent_usage = DailyUsage.query.filter(
        DailyUsage.user_id == current_user.id,
        DailyUsage.date >= seven_days_ago
    ).all()
    
    daily_data = {}
    for u in recent_usage:
        if u.date not in daily_data:
            daily_data[u.date] = {}
        daily_data[u.date][u.feature] = u.count
    
    # 总使用次数
    total_usage = DailyUsage.query.filter_by(user_id=current_user.id).with_entities(
        db.func.sum(DailyUsage.count)
    ).scalar() or 0
    
    # 连续学习天数
    dates_used = db.session.query(db.distinct(DailyUsage.date)).filter_by(
        user_id=current_user.id
    ).order_by(DailyUsage.date.desc()).all()
    streak = 0
    check_date = datetime.now().date()
    date_set = set(d[0] for d in dates_used)
    while check_date.strftime('%Y-%m-%d') in date_set:
        streak += 1
        check_date -= timedelta(days=1)
    
    return jsonify({
        'total_wrong': total_wrong,
        'mastered_wrong': mastered_wrong,
        'total_usage': int(total_usage),
        'streak': streak,
        'daily_data': daily_data
    })

# ===== 错题本页面 =====
@app.route('/wrong_book')
@login_required
def wrong_book():
    return render_template('wrong_book.html')

# ===== 学习数据页面 =====
@app.route('/study_dashboard')
@login_required
def study_dashboard():
    return render_template('study_dashboard.html')




# ===== AI独立站 注册/登录 =====
@app.route('/ai/register', methods=['GET','POST'])
def ai_register():
    if request.method == 'POST':
        phone = request.form.get('phone','').strip()
        name = request.form.get('name','').strip()
        password = request.form.get('password','').strip()
        if not phone or not name or not password:
            flash('请填写所有字段')
            return redirect('/ai/register')
        if not (phone.isdigit() and len(phone) == 11):
            flash('手机号格式不正确（11位数字）')
            return redirect('/ai/register')
        if len(password) < 6:
            flash('密码至少6位')
            return redirect('/ai/register')
        if AIUser.query.filter_by(phone=phone).first():
            flash('该手机号已注册，请直接登录')
            return redirect('/ai/login')
        user = AIUser(phone=phone, name=name, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect('/ai')
    return render_template('ai_register.html')

@app.route('/ai/login', methods=['GET','POST'])
def ai_login():
    if request.method == 'POST':
        phone = request.form.get('phone','').strip()
        password = request.form.get('password','').strip()
        user = AIUser.query.filter_by(phone=phone).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash('手机号或密码错误')
            return redirect('/ai/login')
        login_user(user)
        return redirect('/ai')
    return render_template('ai_login.html')

@app.route('/ai/logout')
def ai_logout():
    logout_user()
    return redirect('/ai')



# ===== 找搭子路由 =====
@app.route('/buddy')
@login_required
def buddy_list():
    category = request.args.get('category', '')
    if category:
        posts = BuddyPost.query.filter_by(category=category).filter(BuddyPost.status!='已关闭').order_by(BuddyPost.created_at.desc()).all()
    else:
        posts = BuddyPost.query.filter(BuddyPost.status!='已关闭').order_by(BuddyPost.created_at.desc()).all()
    return render_template('buddy_list.html', posts=posts, categories=BUDDY_CATEGORIES, current_category=category)

@app.route('/buddy/new', methods=['GET','POST'])
@login_required
def buddy_new():
    if request.method == 'POST':
        post = BuddyPost(
            user_id=current_user.id,
            category=request.form.get('category','其他'),
            title=request.form.get('title','').strip(),
            content=request.form.get('content','').strip(),
            time_info=request.form.get('time_info','').strip(),
            location=request.form.get('location','').strip(),
            max_people=int(request.form.get('max_people',1))
        )
        if not post.title or not post.content:
            flash('标题和内容不能为空')
            return redirect('/buddy/new')
        db.session.add(post)
        db.session.commit()
        flash('发布成功！')
        return redirect('/buddy')
    return render_template('buddy_new.html', categories=BUDDY_CATEGORIES)

@app.route('/buddy/<int:post_id>')
@login_required
def buddy_detail(post_id):
    post = db.session.get(BuddyPost, post_id)
    if not post:
        flash('帖子不存在')
        return redirect('/buddy')
    # 获取与该帖子相关的私信
    messages = BuddyMessage.query.filter_by(post_id=post_id).filter(
        db.or_(
            BuddyMessage.sender_id==current_user.id,
            BuddyMessage.receiver_id==current_user.id
        )
    ).order_by(BuddyMessage.created_at.asc()).all()
    # 检查是否有微信交换请求
    wx_request = WechatExchange.query.filter(
        db.or_(
            db.and_(WechatExchange.requester_id==current_user.id, WechatExchange.target_id==post.user_id),
            db.and_(WechatExchange.requester_id==post.user_id, WechatExchange.target_id==current_user.id)
        )
    ).first()
    return render_template('buddy_detail.html', post=post, messages=messages, wx_request=wx_request)

@app.route('/buddy/<int:post_id>/message', methods=['POST'])
@login_required
def buddy_send_message(post_id):
    post = db.session.get(BuddyPost, post_id)
    if not post:
        return redirect('/buddy')
    content = request.form.get('content','').strip()
    if content:
        receiver_id = post.user_id if post.user_id != current_user.id else int(request.form.get('receiver_id', 0))
        if receiver_id and receiver_id != current_user.id:
            msg = BuddyMessage(post_id=post_id, sender_id=current_user.id, receiver_id=receiver_id, content=content)
            db.session.add(msg)
            db.session.commit()
    return redirect(f'/buddy/{post_id}')

@app.route('/buddy/chat/<int:user_id>')
@login_required
def buddy_chat(user_id):
    other = db.session.get(User, user_id)
    if not other:
        flash('用户不存在')
        return redirect('/buddy')
    messages = BuddyMessage.query.filter(
        db.or_(
            db.and_(BuddyMessage.sender_id==current_user.id, BuddyMessage.receiver_id==user_id),
            db.and_(BuddyMessage.sender_id==user_id, BuddyMessage.receiver_id==current_user.id)
        )
    ).order_by(BuddyMessage.created_at.asc()).all()
    # 标记已读
    BuddyMessage.query.filter_by(sender_id=user_id, receiver_id=current_user.id, is_read=False).update({'is_read':True})
    db.session.commit()
    wx_request = WechatExchange.query.filter(
        db.or_(
            db.and_(WechatExchange.requester_id==current_user.id, WechatExchange.target_id==user_id),
            db.and_(WechatExchange.requester_id==user_id, WechatExchange.target_id==current_user.id)
        )
    ).first()
    return render_template('buddy_chat.html', other=other, messages=messages, wx_request=wx_request)

@app.route('/buddy/chat/<int:user_id>/send', methods=['POST'])
@login_required
def buddy_chat_send(user_id):
    content = request.form.get('content','').strip()
    if content:
        msg = BuddyMessage(sender_id=current_user.id, receiver_id=user_id, content=content)
        db.session.add(msg)
        db.session.commit()
    return redirect(f'/buddy/chat/{user_id}')

@app.route('/buddy/wechat_request/<int:target_id>', methods=['POST'])
@login_required
def wechat_request(target_id):
    if target_id == current_user.id:
        return redirect('/buddy')
    existing = WechatExchange.query.filter(
        db.or_(
            db.and_(WechatExchange.requester_id==current_user.id, WechatExchange.target_id==target_id),
            db.and_(WechatExchange.requester_id==target_id, WechatExchange.target_id==current_user.id)
        )
    ).first()
    if existing:
        flash('已有交换请求')
    else:
        wechat = request.form.get('wechat','').strip()
        if not wechat:
            flash('请填写你的微信号')
            return redirect(request.referrer or '/buddy')
        wx = WechatExchange(requester_id=current_user.id, target_id=target_id, requester_wechat=wechat)
        db.session.add(wx)
        db.session.commit()
        flash('微信交换请求已发送，等待对方同意')
    return redirect(request.referrer or '/buddy')

@app.route('/buddy/wechat_respond/<int:exchange_id>', methods=['POST'])
@login_required
def wechat_respond(exchange_id):
    wx = db.session.get(WechatExchange, exchange_id)
    if not wx or wx.target_id != current_user.id:
        flash('无效请求')
        return redirect('/buddy')
    action = request.form.get('action')
    if action == 'accept':
        wechat = request.form.get('wechat','').strip()
        if not wechat:
            flash('请填写你的微信号')
            return redirect(request.referrer or '/buddy')
        wx.target_wechat = wechat
        wx.status = 'accepted'
        db.session.commit()
        flash('已同意交换微信！')
    elif action == 'reject':
        wx.status = 'rejected'
        db.session.commit()
        flash('已拒绝')
    return redirect(request.referrer or '/buddy')

@app.route('/buddy/<int:post_id>/close')
@login_required
def buddy_close(post_id):
    post = db.session.get(BuddyPost, post_id)
    if post and post.user_id == current_user.id:
        post.status = '已关闭'
        db.session.commit()
        flash('帖子已关闭')
    return redirect('/buddy')

@app.route('/buddy/my_messages')
@login_required
def buddy_my_messages():
    # 获取所有与我相关的聊天用户
    sent = db.session.query(BuddyMessage.receiver_id).filter_by(sender_id=current_user.id).distinct().all()
    received = db.session.query(BuddyMessage.sender_id).filter_by(receiver_id=current_user.id).distinct().all()
    user_ids = set([r[0] for r in sent] + [r[0] for r in received])
    chats = []
    for uid in user_ids:
        u = db.session.get(User, uid)
        if not u: continue
        last_msg = BuddyMessage.query.filter(
            db.or_(
                db.and_(BuddyMessage.sender_id==current_user.id, BuddyMessage.receiver_id==uid),
                db.and_(BuddyMessage.sender_id==uid, BuddyMessage.receiver_id==current_user.id)
            )
        ).order_by(BuddyMessage.created_at.desc()).first()
        unread = BuddyMessage.query.filter_by(sender_id=uid, receiver_id=current_user.id, is_read=False).count()
        chats.append({'user': u, 'last_msg': last_msg, 'unread': unread})
    chats.sort(key=lambda x: x['last_msg'].created_at if x['last_msg'] else datetime.min, reverse=True)
    # 微信交换请求
    wx_pending = WechatExchange.query.filter_by(target_id=current_user.id, status='pending').all()
    wx_accepted = WechatExchange.query.filter(
        db.and_(WechatExchange.status=='accepted',
                db.or_(WechatExchange.requester_id==current_user.id, WechatExchange.target_id==current_user.id))
    ).all()
    return render_template('buddy_messages.html', chats=chats, wx_pending=wx_pending, wx_accepted=wx_accepted)

# ===== 独立AI学习入口（不需要登录）=====
@app.route('/ai')
def ai_standalone():
    return render_template('ai_standalone.html')

@app.route('/api/ai_check_usage', methods=['POST'])
def ai_check_usage():
    """独立AI入口的使用限制（基于session）"""
    from flask import session
    import time
    key = 'ai_usage_' + str(int(time.time() // 86400))
    if key not in session:
        session[key] = 0
    session[key] += 1
    remaining = max(0, 20 - session[key])
    return jsonify({'remaining': remaining, 'allowed': session[key] <= 20})
