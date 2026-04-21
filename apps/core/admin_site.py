from django.contrib.admin import AdminSite


class PaliAdminSite(AdminSite):
    site_header = "PaliGames Admin"
    site_title = "PaliGames Admin"
    index_title = "Администрирование"

    APP_ORDER = {
        "orders": 10,
        "payments": 20,
        "access": 30,
        "products": 40,
        "custom_games": 50,
        "promocodes": 60,
        "favorites": 70,
        "users": 80,
        "notifications": 90,
        "pages": 100,
        "cart": 110,
        "core": 120,
    }

    MODEL_ORDER = {
        "orders": {
            "Order": 10,
            "OrderItem": 20,
        },
        "payments": {
            "Invoice": 10,
            "PaymentEvent": 20,
        },
        "access": {
            "UserProductAccess": 10,
            "GuestAccess": 20,
        },
        "products": {
            "Product": 10,
            "Review": 20,
            "ProductFile": 30,
            "ProductImage": 40,
            "Category": 50,
            "SubType": 60,
            "AgeGroupTag": 70,
            "DevelopmentAreaTag": 80,
            "Theme": 90,
        },
        "custom_games": {
            "CustomGameRequest": 10,
            "CustomGameFile": 20,
            "CustomGameDownloadToken": 30,
        },
        "promocodes": {
            "PromoCode": 10,
            "PromoCodeRedemption": 20,
        },
        "favorites": {
            "Favorite": 10,
        },
        "users": {
            "CustomUser": 10,
        },
        "notifications": {
            "NotificationOutbox": 10,
        },
    }

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request, app_label)

        def app_sort_key(app):
            return (
                self.APP_ORDER.get(app["app_label"], 999),
                app["name"].lower(),
            )

        for app in app_list:
            model_order = self.MODEL_ORDER.get(app["app_label"], {})
            app["models"].sort(
                key=lambda model: (
                    model_order.get(model["object_name"], 999),
                    model["name"].lower(),
                ),
            )

        return sorted(app_list, key=app_sort_key)


admin_site = PaliAdminSite(name="admin")
