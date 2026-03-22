from django.urls import path

from .views import ExpressPayNotificationView, ExpressPaySettlementNotificationView

urlpatterns = [
    path("payments/express-pay/notification/", ExpressPayNotificationView.as_view(), name="express-pay-notification"),
    path(
        "payments/express-pay/settlement-notification/",
        ExpressPaySettlementNotificationView.as_view(),
        name="express-pay-settlement-notification",
    ),
]
