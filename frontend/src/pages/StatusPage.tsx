import { formatServerDateTime } from '../lib/serverTime';
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock3,
  Mail,
  RefreshCw,
  Wrench,
  XCircle,
} from 'lucide-react';

import { statusApi } from '../api';
import { Badge } from '../components/ui/Badge';
import { Card, CardContent, CardHeader } from '../components/ui/Card';
import type { StatusComponent, StatusIncident, StatusService } from '../types';

const statusVariant = (status: string): 'success' | 'warning' | 'danger' | 'gray' => {
  if (status === 'operational' || status === 'resolved') {
    return 'success';
  }
  if (status === 'degraded') {
    return 'warning';
  }
  if (status === 'outage') {
    return 'danger';
  }
  return 'gray';
};

const statusLabel = (status: string) =>
  status
    .split('_')
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(' ');

const serviceIcon = (serviceId: string) => {
  if (serviceId === 'mail-sync') {
    return <Mail size={18} className="text-primary" />;
  }
  if (serviceId === 'planner-sync') {
    return <Wrench size={18} className="text-primary" />;
  }
  if (serviceId === 'llm') {
    return <Bot size={18} className="text-primary" />;
  }
  return <Activity size={18} className="text-primary" />;
};

const statusIcon = (status: string) => {
  if (status === 'operational' || status === 'resolved') {
    return <CheckCircle2 size={18} className="text-green-500" />;
  }
  if (status === 'degraded') {
    return <AlertTriangle size={18} className="text-yellow-500" />;
  }
  if (status === 'outage') {
    return <XCircle size={18} className="text-red-500" />;
  }
  return <Clock3 size={18} className="text-gray-400" />;
};

const formatTimestamp = (value?: string | null) => {
  if (!value) {
    return 'No timestamp yet';
  }
  return formatServerDateTime(value) ?? value;
};

const ComponentRow: React.FC<{ component: StatusComponent }> = ({ component }) => (
  <div className="flex flex-col gap-3 rounded-2xl border border-gray-100 dark:border-gray-800 p-4 md:flex-row md:items-start md:justify-between">
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        {statusIcon(component.status)}
        <p className="font-semibold">{component.name}</p>
        <Badge variant={statusVariant(component.status)}>{statusLabel(component.status)}</Badge>
      </div>
      {component.subtitle && <p className="text-sm text-gray-500 dark:text-gray-400">{component.subtitle}</p>}
      <p className="text-sm text-gray-600 dark:text-gray-300">{component.message}</p>
    </div>
    <p className="text-xs text-gray-400">Updated {formatTimestamp(component.updated_at)}</p>
  </div>
);

const ServiceCard: React.FC<{ service: StatusService }> = ({ service }) => (
  <Card>
    <CardHeader className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            {serviceIcon(service.id)}
            <h2 className="text-xl font-bold">{service.name}</h2>
            <Badge variant={statusVariant(service.status)}>{statusLabel(service.status)}</Badge>
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400">{service.summary}</p>
        </div>
        <p className="text-xs text-gray-400">Updated {formatTimestamp(service.updated_at)}</p>
      </div>
    </CardHeader>
    <CardContent className="space-y-3">
      {service.components.length > 0 ? (
        service.components.map((component) => <ComponentRow key={component.id} component={component} />)
      ) : (
        <div className="rounded-2xl border border-dashed border-gray-200 p-4 text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
          No active components are configured for this service yet.
        </div>
      )}
    </CardContent>
  </Card>
);

const IncidentRow: React.FC<{ incident: StatusIncident }> = ({ incident }) => (
  <div className="flex flex-col gap-2 rounded-2xl border border-gray-100 dark:border-gray-800 p-4">
    <div className="flex flex-wrap items-center gap-2">
      {statusIcon(incident.status)}
      <p className="font-semibold">{incident.title}</p>
      <Badge variant={statusVariant(incident.status)}>{statusLabel(incident.status)}</Badge>
    </div>
    {incident.component_name && <p className="text-sm text-gray-500 dark:text-gray-400">{incident.component_name}</p>}
    <p className="text-sm text-gray-600 dark:text-gray-300">{incident.message}</p>
    <p className="text-xs text-gray-400">{formatTimestamp(incident.timestamp)}</p>
  </div>
);

const LoadingState: React.FC = () => (
  <div className="space-y-6 animate-pulse">
    <div className="h-28 rounded-2xl bg-gray-100 dark:bg-gray-800" />
    <div className="grid gap-6">
      <div className="h-64 rounded-2xl bg-gray-100 dark:bg-gray-800" />
      <div className="h-64 rounded-2xl bg-gray-100 dark:bg-gray-800" />
    </div>
  </div>
);

const StatusPage: React.FC = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ['status-summary'],
    queryFn: async () => (await statusApi.getSummary()).data,
    refetchInterval: 30000,
  });

  if (isLoading) {
    return <LoadingState />;
  }

  if (error || !data) {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
        Failed to load system status.
      </div>
    );
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-3xl font-bold tracking-tight">
            <Activity size={28} className="text-primary" />
            System Status
          </h1>
          <p className="mt-1 text-gray-500 dark:text-gray-400">
            Live health for Janus services and recent incident history.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          <RefreshCw size={16} />
          Updated {formatTimestamp(data.generated_at)}
        </div>
      </div>

      <Card className="overflow-hidden">
        <CardHeader className="space-y-3">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-3">
              {statusIcon(data.overall_status)}
              <div>
                <p className="text-sm font-bold uppercase tracking-[0.2em] text-gray-400">Overall status</p>
                <h2 className="text-2xl font-bold">{statusLabel(data.overall_status)}</h2>
              </div>
            </div>
            <Badge variant={statusVariant(data.overall_status)} className="w-fit">
              {statusLabel(data.overall_status)}
            </Badge>
          </div>
          <p className="text-sm text-gray-600 dark:text-gray-300">{data.overall_message}</p>
        </CardHeader>
      </Card>

      <div className="grid gap-6">
        {data.services.map((service) => (
          <ServiceCard key={service.id} service={service} />
        ))}
      </div>

      <Card>
        <CardHeader>
          <h2 className="text-xl font-bold">Recent incidents</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Recent degradations, outages, and recoveries recorded by Janus.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          {data.recent_incidents.length > 0 ? (
            data.recent_incidents.map((incident) => <IncidentRow key={incident.id} incident={incident} />)
          ) : (
            <div className="rounded-2xl border border-dashed border-gray-200 p-4 text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
              No incidents have been recorded recently.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default StatusPage;
