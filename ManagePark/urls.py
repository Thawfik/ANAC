from django.urls import path
from ManagePark import views

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),

    path('vols/', views.VolListView.as_view(), name='vol_list'),
    path('vols/creer/', views.VolCreateView.as_view(), name='vol_create'),
    path('vols/<uuid:pk>/', views.VolDetailView.as_view(), name='vol_detail'),
    path('vols/<uuid:pk>/modifier/', views.VolUpdateView.as_view(), name='vol_update'),
    path('vols/<uuid:pk>/supprimer/', views.VolDeleteView.as_view(), name='vol_delete'),

    # URLs pour les Stands
    path('stands/', views.StandListView.as_view(), name='stand_list'),
    path('stands/creer/', views.StandCreateView.as_view(), name='stand_create'),
    path('stands/<uuid:pk>/', views.StandDetailView.as_view(), name='stand_detail'),
    path('stands/<uuid:pk>/modifier/', views.StandUpdateView.as_view(), name='stand_update'),
    path('stands/<uuid:pk>/supprimer/', views.StandDeleteView.as_view(), name='stand_delete'),
    path('vols/allocation/', views.LancerAllocationView.as_view(), name='allouer_stands'),
    path('vols/<int:vol_pk>/reallouer/', views.reallouer_vol_action, name='reallouer_vol_action'),

    path('incidents/', views.IncidentListView.as_view(), name='incident_list'),

    # Création d'un incident (liée à un stand spécifique)
    path('incidents/creer/', views.IncidentCreateView.as_view(), name='incident_create_general'),
    path('stands/<uuid:stand_pk>/incident/signaler/', views.IncidentCreateView.as_view(), name='incident_create'),
    # Modification/Résolution d'un incident
    path('incidents/<int:pk>/resoudre/', views.IncidentResolutionView.as_view(), name='incident_resolve'),


]
