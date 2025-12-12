from django.urls import path
from ManagePark import views

urlpatterns = [
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),

    path('vols/', views.VolListView.as_view(), name='vol_list'),
    path('vols/creer/', views.VolCreateView.as_view(), name='vol_create'),
    path('vols/<uuid:pk>/', views.VolDetailView.as_view(), name='vol_detail'),
    path('vols/<uuid:pk>/modifier/', views.VolUpdateView.as_view(), name='vol_update'),
    path('vols/<uuid:pk>/supprimer/', views.VolDeleteView.as_view(), name='vol_delete'),

    # URLs pour les Stands
    path('allouer-stands/', views.AllouerStandsView.as_view(), name='allouer_stands'),
    path('stands/', views.StandListView.as_view(), name='stand_list'),
    path('stands/creer/', views.StandCreateView.as_view(), name='stand_create'),
    path('stands/<uuid:pk>/', views.StandDetailView.as_view(), name='stand_detail'),
    path('stands/<uuid:pk>/modifier/', views.StandUpdateView.as_view(), name='stand_update'),
    path('stands/<uuid:pk>/supprimer/', views.StandDeleteView.as_view(), name='stand_delete'),
    path('vols/allocation/', views.LancerAllocationView.as_view(), name='allouer_stands'),
    path('vols/<uuid:vol_pk>/reallouer/', views.ReallouerVolActionView.as_view(), name='reallouer_vol_action'),

   

    # Création d'un incident (liée à un stand spécifique)
   # ✅ Sans stand_pk obligatoire
    path('incidents/', views.IncidentListView.as_view(), name='incident_list'),
    path('incidents/update/<uuid:incident_pk>/', views.IncidentUpdateView.as_view(), name='incident_update'),
    path('incidents/creer/', views.IncidentCreateView.as_view(), name='incident_create_general'),
    path('stands/<uuid:stand_pk>/incident/', views.IncidentCreateView.as_view(), name='incident_create'),
    # Modification/Résolution d'un incident
    path('incidents/<int:pk>/resoudre/', views.IncidentResolutionView.as_view(), name='incident_resolve'),


]
