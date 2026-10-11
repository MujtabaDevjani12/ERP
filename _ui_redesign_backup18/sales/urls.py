from django.urls import path

from . import delivery_views as dv, pos_cart, returns, views

app_name = "sales"

urlpatterns = [
    path("", views.SaleListView.as_view(), name="sale_list"),
    path("<int:pk>/", views.SaleDetailView.as_view(), name="sale_detail"),
    path("<int:pk>/print/", views.invoice_print_view, name="invoice_print"),
    path("<int:pk>/add-payment/", views.sale_add_payment, name="sale_add_payment"),
    path("<int:pk>/feedback/", views.sale_feedback, name="sale_feedback"),
    path("<int:pk>/return/", returns.sale_return_view, name="sale_return"),
    path("pos/find/", returns.pos_find_sale, name="pos_find_sale"),
    path("pos/orders/", returns.pos_orders, name="pos_orders"),
    path("pos/sale/<int:pk>/", returns.pos_sale_info, name="pos_sale_info"),
    path("pos/return/", returns.pos_do_return, name="pos_do_return"),
    path("pos/finalize/", views.pos_finalize_sale, name="pos_finalize"),
    path("pos/cart/", pos_cart.cart_get, name="pos_cart_get"),
    path("pos/cart/save/", pos_cart.cart_save, name="pos_cart_save"),
    path("pos/cart/clear/", pos_cart.cart_clear, name="pos_cart_clear"),
    path("pos/stock-check/", pos_cart.stock_check, name="pos_stock_check"),
    path("pos/quote/", pos_cart.quote, name="pos_quote"),
    path("pos/feedback/", views.pos_feedback, name="pos_feedback"),
    path("feedback/", views.FeedbackListView.as_view(), name="feedback_list"),
    path("pos/held/", views.held_sales_api, name="held_sales_api"),
    path("pos/held/<int:pk>/resume/", views.resume_held_sale, name="resume_held_sale"),

    # deliveries
    path("deliveries/", dv.DeliveryListView.as_view(), name="delivery_list"),
    path("deliveries/new/", dv.delivery_form, name="delivery_add"),
    path("deliveries/<int:pk>/", dv.delivery_detail, name="delivery_detail"),
    path("deliveries/<int:pk>/edit/", dv.delivery_form, name="delivery_edit"),
    path("deliveries/<int:pk>/status/", dv.delivery_status, name="delivery_status"),
    path("deliveries/<int:pk>/delete/", dv.delivery_delete, name="delivery_delete"),
    path("deliveries/<int:pk>/note/", dv.delivery_note, name="delivery_note"),
    path("deliveries/<int:pk>/email/", dv.delivery_email, name="delivery_email"),
]
