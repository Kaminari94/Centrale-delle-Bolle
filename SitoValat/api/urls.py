from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health, name="api_health"),

    # bolle
    path("bolle/", views.bolle_list, name="api_bolle_list"),
    path("bolle/<int:pk>/", views.bolle_detail, name="api_bolle_detail"),
    path("bolle/<int:pk>/receipt/", views.receipt_view, name="api_receipt"),
    path("customers/", views.customers_list),
    path("bolle/quick/", views.bolle_quick_create),
]