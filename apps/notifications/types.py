from enum import StrEnum


class NotificationType(StrEnum):
    GUEST_ORDER_DOWNLOAD = "guest_order_download"
    CUSTOM_GAME_DOWNLOAD = "custom_game_download"
    ORDER_REWARD_USER = "order_reward_user"
    REVIEW_REWARD_USER = "review_reward_user"
    REVIEW_REJECTED_USER = "review_rejected_user"
    REVIEW_SUBMITTED_ADMIN = "review_submitted_admin"
    CUSTOM_GAME_REQUEST_CUSTOMER = "custom_game_request_customer"
    CUSTOM_GAME_REQUEST_ADMIN = "custom_game_request_admin"
